"""Talks to the read-only chatbot: the deployed runtime through boto3 (signed with the website's AWS role), or a local
`agentcore dev --agent chatbot --port 8090` over plain HTTP. Replaces web/server.py's Chatbot and the old Node API."""
import json
import logging
import urllib.error
import urllib.request
import boto3
from botocore.config import Config
from django.conf import settings
# A turn can run for minutes (several lookups, a long answer)
READ_TIMEOUT_SECONDS = 300
log = logging.getLogger(__name__)
_client = None


def _events_from_lines(lines) -> list[dict]:
    """The chatbot's streamed events: every `data: {...}` line of its answer."""
    events = []
    for raw in lines:
        line = raw.decode("utf-8").strip() if isinstance(raw, bytes) else raw.strip()
        if line.startswith("data: "):
            events.append(json.loads(line[6:]))
    return events


def _invoke_deployed(session_id: str, payload: dict) -> list[dict]:
    global _client
    if _client is None:
        region = settings.CHATBOT_RUNTIME_ARN.split(":")[3]
        _client = boto3.client("bedrock-agentcore", region_name=region,
                               config=Config(read_timeout=READ_TIMEOUT_SECONDS, retries={"max_attempts": 1}))
    response = _client.invoke_agent_runtime(
        agentRuntimeArn=settings.CHATBOT_RUNTIME_ARN,
        runtimeSessionId=session_id,
        payload=json.dumps(payload).encode(),
        contentType="application/json",
        accept="text/event-stream, application/json"
    )
    return _events_from_lines(response["response"].iter_lines())


def _invoke_local(session_id: str, payload: dict) -> list[dict]:
    request = urllib.request.Request(
        settings.CHATBOT_URL,
        data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json", "X-Amzn-Bedrock-AgentCore-Runtime-Session-Id": session_id}
    )
    with urllib.request.urlopen(request, timeout=READ_TIMEOUT_SECONDS) as response:
        return _events_from_lines(response)


def describe() -> str:
    """Which chatbot the page talks to, shown in its header when running locally."""
    if settings.CHATBOT_RUNTIME_ARN:
        return ""
    return f"local chatbot ({settings.CHATBOT_URL})"


def events(session_id: str, payload: dict) -> tuple[list[dict], list[str]]:
    """The chatbot's events for one request, or a short reason it couldn't be called."""
    try:
        if settings.CHATBOT_RUNTIME_ARN:
            return _invoke_deployed(session_id, payload), []
        return _invoke_local(session_id, payload), []
    except urllib.error.HTTPError as e:
        return [], [f"The chatbot returned an error: HTTP {e.code} {e.reason}."]
    except (urllib.error.URLError, OSError) as e:
        return [], [f"Can't reach the chatbot ({e}). Start it with `agentcore dev --agent chatbot --port 8090` in "
                    "TranscriptAgent/."]
    except Exception:
        # botocore errors (access denied, throttling); the details stay in the log
        log.exception("Calling the deployed chatbot failed")
        return [], ["The archive assistant could not be reached. Try again in a moment."]


def ask(session_id: str, prompt: str) -> dict:
    """One question, shaped for the page: replies and full transcripts in order, and errors."""
    found, errors = events(session_id, {"prompt": prompt})
    items = []
    for event in found:
        if event.get("error"):
            errors.append(str(event["error"]))
        elif event.get("type") == "transcript":
            items.append({"kind": "transcript", "title": event.get("title"), "meeting_date": event.get("meeting_date"),
                          "transcript": event.get("transcript")})
        elif event.get("type") == "ai" and event.get("text"):
            items.append({"kind": "reply", "text": event["text"]})
    return {"items": items, "errors": errors}


def recent(session_id: str, count: int) -> dict:
    """The latest meetings for the page's side panel, which the chatbot looks up without the model."""
    found, errors = events(session_id, {"recent_meetings": count})
    meetings = next((event["meetings"] for event in found if event.get("type") == "recent_meetings"), [])
    return {"meetings": meetings, "errors": errors + [str(event["error"]) for event in found if event.get("error")]}
