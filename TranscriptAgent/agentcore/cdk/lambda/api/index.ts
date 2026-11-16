/**
 * The hosted front end's API, behind CloudFront at /api/*. It calls the agent runtime for the page. There is no
 * sign-in: each conversation is kept apart only by its session id, a random value the browser makes.
 *
 *   GET  /api/status   the upload limits
 *   POST /api/chat     { "prompt": "..." } or { "confirm": true|false }, with an X-Session-Id header
 *
 * The response streams: a space every few seconds while the agent works (so CloudFront's origin timeout doesn't cut
 * a long turn off), then one JSON object. JSON.parse ignores the leading spaces.
 */
import { BedrockAgentCoreClient, InvokeAgentRuntimeCommand } from '@aws-sdk/client-bedrock-agentcore';
import type { LambdaFunctionURLEvent } from 'aws-lambda';
import type { Writable } from 'stream';

declare const awslambda: {
  streamifyResponse(
    handler: (event: LambdaFunctionURLEvent, responseStream: Writable) => Promise<void>,
  ): unknown;
  HttpResponseStream: {
    from(stream: Writable, metadata: { statusCode: number; headers: Record<string, string> }): Writable;
  };
};

const RUNTIME_ARN = process.env.RUNTIME_ARN!;
const MAX_BYTES = 500 * 1024 * 1024;
const EXTENSIONS = ['.mp3', '.wav'];
const KEEPALIVE_MS = 10_000;
const MAX_PROMPT_CHARS = 20_000;
const SESSION_ID = /^[A-Za-z0-9-]{33,100}$/;

const agentcore = new BedrockAgentCoreClient({});

interface AgentEvent {
  [key: string]: unknown;
  error?: string;
  type?: string;
  text?: string;
  tool?: string;
  question?: string;
}

type Item =
  | { kind: 'reply'; text: string }
  | { kind: 'transcript'; title: unknown; meeting_date: unknown; transcript: unknown };

function escapeRegExp(text: string): string {
  return text.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
}

/** The reply with every copy of the upload link cut out, as the terminal chat does: the page uploads by itself. */
function withoutLink(text: string, url: string): string {
  const link = escapeRegExp(url.split('?', 1)[0]) + "(?:[^\\s<>()`*\\[\\]]*[^\\s<>()`*\\[\\],.;:!?'\"])?";
  const wrap = '(?:`|\\*\\*|\\*|__|_)?';
  let cut = text.replace(new RegExp(`${wrap}\\[[^\\]]*\\]\\(<?${link}>?\\)${wrap}`, 'g'), '');
  cut = cut.replace(new RegExp(`${wrap}<?${link}>?${wrap}`, 'g'), '');
  if (cut === text) return text.trim();
  return cut.replace(/[ \t]+([,.;:!?])/g, '$1').replace(/[ \t]{2,}/g, ' ').trim();
}

/** The agent's streamed events, shaped like the local web server's responses (plus the upload link itself). */
function summarize(events: AgentEvent[]) {
  const items: Item[] = [];
  const errors: string[] = [];
  let confirmation: string | null = null;
  let uploadUrl: string | null = null;
  let audioKey: string | null = null;
  for (const event of events) {
    if (event.error) {
      errors.push(String(event.error));
    } else if (event.type === 'confirmation_required') {
      confirmation = String(event.question ?? '').replace(' Reply yes to continue or no to cancel.', '') || null;
    } else if (event.type === 'transcript') {
      items.push({ kind: 'transcript', title: event.title, meeting_date: event.meeting_date, transcript: event.transcript });
    } else if (event.type === 'ai' && event.text) {
      items.push({ kind: 'reply', text: String(event.text) });
    } else if (event.type === 'tool' && event.tool === 'request_audio_upload') {
      try {
        const handedOut = JSON.parse(String(event.text));
        uploadUrl = handedOut.upload_url ?? null;
        audioKey = handedOut.audio_key ?? null;
      } catch {
        // not a link (the tool refused the file type, say); the agent's reply explains
      }
    }
  }
  const shown = uploadUrl
    ? items
        .map((item) => (item.kind === 'reply' ? { ...item, text: withoutLink(item.text, uploadUrl!) } : item))
        .filter((item) => item.kind !== 'reply' || item.text)
    : items;
  const dot = audioKey ? audioKey.lastIndexOf('.') : -1;
  return {
    items: shown,
    confirmation,
    upload_ready: Boolean(uploadUrl),
    upload_url: uploadUrl,
    upload_extension: uploadUrl && dot >= 0 ? audioKey!.slice(dot).toLowerCase() : null,
    errors,
  };
}

async function invokeAgent(sessionId: string, payload: Record<string, unknown>): Promise<AgentEvent[]> {
  const output = await agentcore.send(
    new InvokeAgentRuntimeCommand({
      agentRuntimeArn: RUNTIME_ARN,
      runtimeSessionId: sessionId,
      payload: new TextEncoder().encode(JSON.stringify(payload)),
      contentType: 'application/json',
      accept: 'text/event-stream, application/json',
    }),
  );
  const body = (await output.response?.transformToString()) ?? '';
  const events: AgentEvent[] = [];
  for (const raw of body.split('\n')) {
    const line = raw.trim();
    if (line.startsWith('data: ')) events.push(JSON.parse(line.slice(6)));
  }
  if (!events.length && body.trim().startsWith('{')) events.push(JSON.parse(body));
  return events;
}

function header(event: LambdaFunctionURLEvent, name: string): string | undefined {
  return event.headers?.[name.toLowerCase()];
}

export const handler = awslambda.streamifyResponse(async (event, responseStream) => {
  const respond = (statusCode: number) =>
    awslambda.HttpResponseStream.from(responseStream, {
      statusCode,
      headers: { 'Content-Type': 'application/json', 'Cache-Control': 'no-store' },
    });
  const finish = (statusCode: number, body: unknown) => {
    const stream = respond(statusCode);
    stream.write(JSON.stringify(body));
    stream.end();
  };

  const method = event.requestContext.http.method;
  const path = event.rawPath;

  if (method === 'GET' && path === '/api/status') {
    finish(200, { agent: 'deployed agent', max_bytes: MAX_BYTES, extensions: EXTENSIONS });
    return;
  }
  if (method !== 'POST' || path !== '/api/chat') {
    finish(404, { errors: ['Not found'] });
    return;
  }

  const sessionId = header(event, 'X-Session-Id') ?? '';
  if (!SESSION_ID.test(sessionId)) {
    finish(400, { errors: ['Missing or invalid X-Session-Id header'] });
    return;
  }
  let request: Record<string, unknown>;
  try {
    const body = event.isBase64Encoded ? Buffer.from(event.body ?? '', 'base64').toString('utf8') : event.body ?? '';
    request = JSON.parse(body || '{}');
  } catch {
    finish(400, { errors: ['The request body must be JSON'] });
    return;
  }
  const payload: Record<string, unknown> = {};
  if (typeof request.prompt === 'string' && request.prompt.length <= MAX_PROMPT_CHARS) {
    payload.prompt = request.prompt;
  } else if (typeof request.confirm === 'boolean') {
    payload.confirm = request.confirm;
  } else {
    finish(400, { errors: [`Send a 'prompt' (up to ${MAX_PROMPT_CHARS} characters) or a 'confirm' true/false`] });
    return;
  }

  const stream = respond(200);
  const keepalive = setInterval(() => stream.write(' '), KEEPALIVE_MS);
  let result;
  try {
    result = summarize(await invokeAgent(sessionId, payload));
  } catch (error) {
    console.error('InvokeAgentRuntime failed', error);
    result = { errors: ['The agent could not be reached. Try again in a moment.'] };
  } finally {
    clearInterval(keepalive);
  }
  stream.write(JSON.stringify(result));
  stream.end();
});
