"""A small web front end for the read-only public agent, served on this computer only.

    python server.py                                   talks to `agentcore dev --agent public_agent --port 8090`
    python server.py --runtime-arn arn:aws:...         talks to the deployed public_agent with your AWS credentials

Then open http://127.0.0.1:8501. The server forwards chat messages to the agent. The deployed mode needs boto3: run
it as `uv run --project ../app/agent python server.py --runtime-arn ...`.
"""
import argparse
import json
import os
import sys
import urllib.error
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "cli"))
from client import invoke, summarize  # noqa: E402

PAGE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "index.html")
# The public agent's local address: `agentcore dev --agent public_agent --port 8090`. Not found by scanning ports,
# so the page never ends up talking to the clerks' agent by accident
PUBLIC_AGENT_URL = "http://127.0.0.1:8090/invocations"


class Agent:
    """Sends payloads to the public agent: a local `agentcore dev`, or the deployed runtime."""

    def __init__(self, agent_url: str, runtime_arn: str | None):
        self.agent_url = agent_url
        self.runtime_arn = runtime_arn
        self._client = None

    def describe(self) -> str:
        if self.runtime_arn:
            return f"deployed runtime {self.runtime_arn.rsplit('/', 1)[-1]}"
        return self.agent_url

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
        """One turn, shaped for the page: replies and transcripts in order, and errors."""
        try:
            if self.runtime_arn:
                events = self._invoke_deployed(session_id, payload)
            else:
                events = invoke(self.agent_url, session_id, payload)
        except urllib.error.HTTPError as e:
            return {"errors": [f"The agent returned an error: HTTP {e.code} {e.reason}."]}
        except (urllib.error.URLError, OSError) as e:
            return {"errors": [f"Can't reach the agent ({e}). Is `agentcore dev --agent public_agent --port 8090` "
                               "running?"]}
        except Exception as e:  # botocore errors (credentials, access denied, throttling)
            return {"errors": [f"The deployed agent couldn't be called: {e}"]}
        result = summarize(events)
        items = []
        for kind, value in result["items"]:
            if kind == "reply":
                items.append({"kind": "reply", "text": value})
            else:
                items.append({"kind": "transcript", "title": value["title"], "meeting_date": value["meeting_date"],
                              "transcript": value["transcript"]})
        return {"items": items, "errors": result["errors"]}


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
            self._json(HTTPStatus.OK, {"agent": self.agent.describe()})
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
            if not isinstance(payload, dict) or not isinstance(payload.get("prompt"), str):
                self._json(HTTPStatus.BAD_REQUEST, {"errors": ["Send a 'prompt'"]})
                return
            self._json(HTTPStatus.OK, self.agent.send(session_id, {"prompt": payload["prompt"]}))
        else:
            self._json(HTTPStatus.NOT_FOUND, {"errors": ["Not found"]})


def main():
    parser = argparse.ArgumentParser(description="Web front end for the meeting transcript agent.")
    parser.add_argument("--port", type=int, default=8501)
    parser.add_argument("--agent-url", default=os.environ.get("PUBLIC_AGENT_URL", PUBLIC_AGENT_URL),
                        help=f"the local public agent's /invocations URL (default: {PUBLIC_AGENT_URL})")
    parser.add_argument("--runtime-arn", default=os.environ.get("PUBLIC_AGENT_RUNTIME_ARN"),
                        help="the deployed public_agent runtime's ARN (`agentcore status` shows it)")
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
