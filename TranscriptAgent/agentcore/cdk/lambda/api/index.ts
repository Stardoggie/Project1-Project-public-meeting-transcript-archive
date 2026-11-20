/**
 * The hosted front end's API, behind CloudFront at /api/*. It sends the page's questions to the read-only chatbot
 * runtime. There is no sign-in: each conversation is kept apart only by its session id, a random value the browser
 * makes.
 *
 *   GET  /api/status   that the API is up
 *   POST /api/chat     { "prompt": "..." }, with an X-Session-Id header
 *   POST /api/recent   the latest meetings, for the page's front (no body; X-Session-Id header). The chatbot looks
 *                      them up without the model.
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
const KEEPALIVE_MS = 10_000;
const MAX_PROMPT_CHARS = 20_000;
const RECENT_MEETINGS = 3;
const SESSION_ID = /^[A-Za-z0-9-]{33,100}$/;

const agentcore = new BedrockAgentCoreClient({});

interface AgentEvent {
  [key: string]: unknown;
  error?: string;
  type?: string;
  text?: string;
}

type Item =
  | { kind: 'reply'; text: string }
  | { kind: 'transcript'; title: unknown; meeting_date: unknown; transcript: unknown };

/** The chatbot's streamed events, shaped like the local web server's responses: replies, transcripts, errors. */
function summarize(events: AgentEvent[]) {
  const items: Item[] = [];
  const errors: string[] = [];
  for (const event of events) {
    if (event.error) {
      errors.push(String(event.error));
    } else if (event.type === 'transcript') {
      items.push({ kind: 'transcript', title: event.title, meeting_date: event.meeting_date, transcript: event.transcript });
    } else if (event.type === 'ai' && event.text) {
      items.push({ kind: 'reply', text: String(event.text) });
    }
  }
  return { items, errors };
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
    finish(200, { agent: 'archive chatbot' });
    return;
  }
  if (method !== 'POST' || (path !== '/api/chat' && path !== '/api/recent')) {
    finish(404, { errors: ['Not found'] });
    return;
  }

  const sessionId = header(event, 'X-Session-Id') ?? '';
  if (!SESSION_ID.test(sessionId)) {
    finish(400, { errors: ['Missing or invalid X-Session-Id header'] });
    return;
  }
  if (path === '/api/recent') {
    try {
      const events = await invokeAgent(sessionId, { recent_meetings: RECENT_MEETINGS });
      const found = events.find((e) => e.type === 'recent_meetings');
      const errors = events.filter((e) => e.error).map((e) => String(e.error));
      finish(200, { meetings: Array.isArray(found?.meetings) ? found.meetings : [], errors });
    } catch (error) {
      console.error('InvokeAgentRuntime failed', error);
      finish(200, { meetings: [], errors: ['The recent meetings could not be loaded.'] });
    }
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
  if (typeof request.prompt !== 'string' || !request.prompt.trim() || request.prompt.length > MAX_PROMPT_CHARS) {
    finish(400, { errors: [`Send a 'prompt' of up to ${MAX_PROMPT_CHARS} characters`] });
    return;
  }
  const payload = { prompt: request.prompt };

  const stream = respond(200);
  const keepalive = setInterval(() => stream.write(' '), KEEPALIVE_MS);
  let result;
  try {
    result = summarize(await invokeAgent(sessionId, payload));
  } catch (error) {
    console.error('InvokeAgentRuntime failed', error);
    result = { errors: ['The archive assistant could not be reached. Try again in a moment.'] };
  } finally {
    clearInterval(keepalive);
  }
  stream.write(JSON.stringify(result));
  stream.end();
});
