"""A small web front end for the archive's read-only chatbot, served on this computer only.

    python server.py                        talks to the chatbot under `agentcore dev --agent chatbot --port 8090`
    python server.py --deployed             talks to the deployed chatbot with your AWS credentials
    python server.py --runtime-arn arn:...  talks to that deployed runtime

Then open http://127.0.0.1:8501. The page only asks questions: adding, changing and deleting meetings and uploading
recordings is the admin agent's job, from the terminal chat (`TranscriptAgent/cli`). The deployed modes need boto3:
run them as `uv run --project ../app/chatbot python server.py --deployed`.
"""
import argparse
import json
import os
import sys
import urllib.error
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "cli"))
from client import find_runtime_arn, invoke, summarize  # noqa: E402

PAGE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "index.html")
# Outside the 8080-8089 range the terminal chat scans, so the terminal chat never finds the chatbot by mistake
CHATBOT_URL = "http://127.0.0.1:8090/invocations"
MAX_PROMPT_CHARS = 20_000


class Chatbot:
    """Sends questions to the chatbot under `agentcore dev`, or to the deployed chatbot runtime."""

    def __init__(self, agent_url: str, runtime_arn: str | None):
        self.agent_url = agent_url
        self.runtime_arn = runtime_arn
        self._client = None

    def describe(self) -> str:
        if self.runtime_arn:
            return f"deployed chatbot {self.runtime_arn.rsplit('/', 1)[-1]}"
        return f"local chatbot ({self.agent_url})"

    def _invoke_deployed(self, session_id: str, payload: dict) -> list[dict]:
        if self._client is None:
            import boto3
            from botocore.config import Config

            region = self.runtime_arn.split(":")[3]
            # A turn can run for minutes (several lookups, a long answer)
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

    def ask(self, session_id: str, prompt: str) -> dict:
        """One question, shaped for the page: replies and full transcripts in order, and errors."""
        payload = {"prompt": prompt}
        try:
            if self.runtime_arn:
                events = self._invoke_deployed(session_id, payload)
            else:
                events = invoke(self.agent_url, session_id, payload)
        except urllib.error.HTTPError as e:
            return {"errors": [f"The chatbot returned an error: HTTP {e.code} {e.reason}."]}
        except (urllib.error.URLError, OSError) as e:
            return {"errors": [f"Can't reach the chatbot ({e}). Start it with "
                               "`agentcore dev --agent chatbot --port 8090` in TranscriptAgent/."]}
        except Exception as e:  # botocore errors (credentials, access denied, throttling)
            return {"errors": [f"The deployed chatbot couldn't be called: {e}"]}
        result = summarize(events)
        items = []
        for kind, value in result["items"]:
            if kind == "reply":
                if value:
                    items.append({"kind": "reply", "text": value})
            else:
                items.append({"kind": "transcript", "title": value["title"], "meeting_date": value["meeting_date"],
                              "transcript": value["transcript"]})
        return {"items": items, "errors": result["errors"]}


class Handler(BaseHTTPRequestHandler):
    chatbot: Chatbot

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
        """Only this page may call the API: another site open in the browser can't drive the chatbot through it."""
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
            self._json(HTTPStatus.OK, {"agent": self.chatbot.describe()})
        else:
            self._json(HTTPStatus.NOT_FOUND, {"errors": ["Not found"]})

    def do_POST(self):
        if not self._same_origin():
            self._json(HTTPStatus.FORBIDDEN, {"errors": ["Cross-site requests are not allowed"]})
            return
        if self.path != "/api/chat":
            self._json(HTTPStatus.NOT_FOUND, {"errors": ["Not found"]})
            return
        session_id = self.headers.get("X-Session-Id", "")
        if len(session_id) < 33:  # AgentCore wants session ids of 33+ characters
            self._json(HTTPStatus.BAD_REQUEST, {"errors": ["Missing or short X-Session-Id header"]})
            return
        length = int(self.headers.get("Content-Length") or 0)
        try:
            payload = json.loads(self.rfile.read(length) or b"{}")
        except ValueError:
            self._json(HTTPStatus.BAD_REQUEST, {"errors": ["The request body must be JSON"]})
            return
        prompt = payload.get("prompt") if isinstance(payload, dict) else None
        if not isinstance(prompt, str) or not prompt.strip() or len(prompt) > MAX_PROMPT_CHARS:
            self._json(HTTPStatus.BAD_REQUEST, {"errors": [f"Send a 'prompt' of up to {MAX_PROMPT_CHARS} characters"]})
            return
        self._json(HTTPStatus.OK, self.chatbot.ask(session_id, prompt))


def main():
    parser = argparse.ArgumentParser(description="Web front end for the archive's read-only chatbot.")
    parser.add_argument("--port", type=int, default=8501)
    parser.add_argument("--agent-url", default=os.environ.get("CHATBOT_URL", CHATBOT_URL),
                        help=f"the local chatbot's /invocations URL (default: {CHATBOT_URL})")
    parser.add_argument("--deployed", action="store_true",
                        help="use the deployed chatbot (found with `agentcore status`)")
    parser.add_argument("--runtime-arn", default=os.environ.get("CHATBOT_RUNTIME_ARN"),
                        help="the deployed chatbot runtime's ARN")
    args = parser.parse_args()

    runtime_arn = args.runtime_arn
    if args.deployed and not runtime_arn:
        runtime_arn = find_runtime_arn("chatbot", "CHATBOT_RUNTIME_ARN")
        if not runtime_arn:
            sys.exit("No deployed chatbot found. Deploy with `agentcore deploy`, or pass --runtime-arn.")
    Handler.chatbot = Chatbot(args.agent_url, runtime_arn)
    server = ThreadingHTTPServer(("127.0.0.1", args.port), Handler)
    print(f"Meeting Transcript Archive on http://127.0.0.1:{args.port}  ({Handler.chatbot.describe()})")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
