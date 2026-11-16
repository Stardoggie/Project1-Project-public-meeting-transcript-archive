"""A small web front end for the meeting transcript agent, served on this computer only.

    python server.py                                   talks to `agentcore dev` (found on ports 8080-8089)
    python server.py --runtime-arn arn:aws:...         talks to the deployed runtime with your AWS credentials

Then open http://127.0.0.1:8501. The server forwards chat messages to the agent and uploads recordings to the
link the agent hands out, so the browser never sees that link and the bucket needs no CORS rules. The deployed mode
needs boto3: run it as `uv run --project ../app/agent python server.py --runtime-arn ...`.
"""
import argparse
import json
import os
import sys
import tempfile
import threading
import urllib.error
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "cli"))
from chat import ALLOWED_EXTENSIONS, MAX_BYTES, without_link  # noqa: E402
from client import find_agent_url, invoke, put_file, summarize  # noqa: E402

PAGE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "index.html")
CHUNK = 1024 * 1024


class Agent:
    """Sends payloads to a local `agentcore dev` or to the deployed runtime, and keeps each session's upload link."""

    def __init__(self, agent_url: str | None, runtime_arn: str | None):
        self.agent_url = agent_url
        self.runtime_arn = runtime_arn
        self.upload_links: dict[str, tuple[str, str | None]] = {}  # session id -> (upload url, audio key)
        self.lock = threading.Lock()
        self._client = None

    def describe(self) -> str:
        if self.runtime_arn:
            return f"deployed runtime {self.runtime_arn.rsplit('/', 1)[-1]}"
        return self.agent_url or "agentcore dev (not found yet)"

    def _invoke_deployed(self, session_id: str, payload: dict) -> list[dict]:
        if self._client is None:
            import boto3
            from botocore.config import Config

            region = self.runtime_arn.split(":")[3]
            # An agent turn can run for minutes (tool calls, a long answer)
            self._client = boto3.client("bedrock-agentcore", region_name=region,
                                        config=Config(read_timeout=300, retries={"max_attempts": 1}))
        response = self._client.invoke_agent_runtime(
            agentRuntimeArn=self.runtime_arn,
            runtimeSessionId=session_id,
            payload=json.dumps(payload).encode(),
            contentType="application/json",
            accept="text/event-stream, application/json",
        )
        events = []
        for raw in response["response"].iter_lines():
            line = raw.decode("utf-8").strip()
            if line.startswith("data: "):
                events.append(json.loads(line[6:]))
        return events

    def send(self, session_id: str, payload: dict) -> dict:
        """One turn, shaped for the page: replies and transcripts in order, a confirmation question, whether the
        agent is waiting for a recording (and which file type), and errors."""
        try:
            if self.runtime_arn:
                events = self._invoke_deployed(session_id, payload)
            else:
                url = self.agent_url or find_agent_url()
                if not url:
                    return {"errors": ["Can't find the agent. Start it with `agentcore dev` in TranscriptAgent/."]}
                events = invoke(url, session_id, payload)
        except urllib.error.HTTPError as e:
            return {"errors": [f"The agent returned an error: HTTP {e.code} {e.reason}."]}
        except (urllib.error.URLError, OSError) as e:
            return {"errors": [f"Can't reach the agent ({e}). Is `agentcore dev` running?"]}
        except Exception as e:  # botocore errors (credentials, access denied, throttling)
            return {"errors": [f"The deployed agent couldn't be called: {e}"]}
        result = summarize(events)
        link = result["upload_url"]
        items = []
        for kind, value in result["items"]:
            if kind == "reply":
                value = without_link(value, link) if link else value
                if value:
                    items.append({"kind": "reply", "text": value})
            else:
                items.append({"kind": "transcript", "title": value["title"], "meeting_date": value["meeting_date"],
                              "transcript": value["transcript"]})
        if link:
            with self.lock:
                self.upload_links[session_id] = (link, result["audio_key"])
        audio_key = result["audio_key"] or ""
        return {
            "items": items,
            "confirmation": (result["confirmation"] or "").replace(" Reply yes to continue or no to cancel.", "")
            or None,
            "upload_ready": bool(link),
            "upload_extension": os.path.splitext(audio_key)[1].lower() if link else None,
            "errors": result["errors"],
        }

    def take_upload_link(self, session_id: str) -> tuple[str, str | None] | None:
        with self.lock:
            return self.upload_links.pop(session_id, None)


class Handler(BaseHTTPRequestHandler):
    agent: Agent

    def log_message(self, format, *args):  # quieter than the default per-request lines
        pass

    def _json(self, status: int, body: dict):
        data = json.dumps(body).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _same_origin(self) -> bool:
        """Only this page may call the API: another site open in the browser can't drive the agent through it."""
        origin = self.headers.get("Origin")
        host = self.headers.get("Host", "")
        # The Host check also stops a site that points its own name at 127.0.0.1 (DNS rebinding)
        if host.rsplit(":", 1)[0] not in ("127.0.0.1", "localhost"):
            return False
        return origin is None or origin == f"http://{host}"

    def do_GET(self):
        if self.path in ("/", "/index.html"):
            with open(PAGE, "rb") as f:
                data = f.read()
            self.send_response(HTTPStatus.OK)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)
        elif self.path == "/api/status":
            self._json(HTTPStatus.OK, {"agent": self.agent.describe(), "max_bytes": MAX_BYTES,
                                       "extensions": sorted(ALLOWED_EXTENSIONS)})
        else:
            self._json(HTTPStatus.NOT_FOUND, {"errors": ["Not found"]})

    def do_POST(self):
        if not self._same_origin():
            self._json(HTTPStatus.FORBIDDEN, {"errors": ["Cross-site requests are not allowed"]})
            return
        session_id = self.headers.get("X-Session-Id", "")
        if len(session_id) < 33:  # AgentCore wants session ids of 33+ characters
            self._json(HTTPStatus.BAD_REQUEST, {"errors": ["Missing or short X-Session-Id header"]})
            return
        length = int(self.headers.get("Content-Length") or 0)
        if self.path == "/api/chat":
            try:
                payload = json.loads(self.rfile.read(length) or b"{}")
            except ValueError:
                self._json(HTTPStatus.BAD_REQUEST, {"errors": ["The request body must be JSON"]})
                return
            if not isinstance(payload, dict) or not ("prompt" in payload or "confirm" in payload):
                self._json(HTTPStatus.BAD_REQUEST, {"errors": ["Send a 'prompt' or a 'confirm'"]})
                return
            self._json(HTTPStatus.OK, self.agent.send(session_id, {k: payload[k] for k in ("prompt", "confirm")
                                                                   if k in payload}))
        elif self.path == "/api/upload":
            self._upload(session_id, length)
        else:
            self._json(HTTPStatus.NOT_FOUND, {"errors": ["Not found"]})

    def _upload(self, session_id: str, length: int):
        """Save the recording the page sends, PUT it to the link the agent handed out, then let the agent attach it."""
        if length <= 0:
            self._json(HTTPStatus.BAD_REQUEST, {"errors": ["That file is empty."]})
            return
        if length > MAX_BYTES:
            self._json(HTTPStatus.REQUEST_ENTITY_TOO_LARGE, {"errors": ["The limit is 500 MB."]})
            return
        link = self.agent.take_upload_link(session_id)
        if not link:
            self.rfile.read(length)
            self._json(HTTPStatus.CONFLICT, {"errors": ["The agent hasn't handed out an upload link for this "
                                                        "conversation yet. Ask it to add a recording first."]})
            return
        url, _ = link
        fd, path = tempfile.mkstemp(suffix=".upload")
        try:
            with os.fdopen(fd, "wb") as f:
                remaining = length
                while remaining:
                    chunk = self.rfile.read(min(CHUNK, remaining))
                    if not chunk:
                        break
                    f.write(chunk)
                    remaining -= len(chunk)
            try:
                status = put_file(url, path)
            except (urllib.error.URLError, OSError) as e:
                self._json(HTTPStatus.BAD_GATEWAY, {"errors": [f"The upload to S3 failed: {e}"]})
                return
            if status != 200:
                self._json(HTTPStatus.BAD_GATEWAY, {"errors": [f"The upload to S3 failed (HTTP {status})."]})
                return
        finally:
            os.remove(path)
        self._json(HTTPStatus.OK, self.agent.send(
            session_id, {"prompt": "The upload is done. Attach it to that meeting and start transcription."}))


def main():
    parser = argparse.ArgumentParser(description="Web front end for the meeting transcript agent.")
    parser.add_argument("--port", type=int, default=8501)
    parser.add_argument("--agent-url", default=os.environ.get("AGENT_URL"),
                        help="a local agent's /invocations URL (default: find `agentcore dev`)")
    parser.add_argument("--runtime-arn", default=os.environ.get("AGENT_RUNTIME_ARN"),
                        help="the deployed runtime's ARN (`agentcore status` shows it)")
    args = parser.parse_args()

    Handler.agent = Agent(args.agent_url, args.runtime_arn)
    server = ThreadingHTTPServer(("127.0.0.1", args.port), Handler)
    print(f"Meeting Transcript Archive on http://127.0.0.1:{args.port}  (agent: {Handler.agent.describe()})")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
