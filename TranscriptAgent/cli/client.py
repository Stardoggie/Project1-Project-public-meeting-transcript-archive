"""Talks to the transcript agent through AgentCore's /invocations endpoint (standard library only)."""
import json
import os
import urllib.error
import urllib.request

# agentcore dev normally uses 8080 but moves to the next free port when 8080 is busy
LOCAL_PORTS = range(8080, 8090)


def find_agent_url() -> str | None:
    """AGENT_URL if set, otherwise the first local port where `agentcore dev` answers /ping."""
    if os.environ.get("AGENT_URL"):
        return os.environ["AGENT_URL"]
    for port in LOCAL_PORTS:
        try:
            with urllib.request.urlopen(f"http://127.0.0.1:{port}/ping", timeout=1) as response:
                if json.load(response).get("status") == "Healthy":
                    return f"http://127.0.0.1:{port}/invocations"
        except (urllib.error.URLError, OSError, ValueError):
            continue
    return None


def invoke(agent_url: str, session_id: str, payload: dict) -> list[dict]:
    """Send one request to the agent and return its streamed events."""
    request = urllib.request.Request(
        agent_url,
        data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json", "X-Amzn-Bedrock-AgentCore-Runtime-Session-Id": session_id},
    )
    events = []
    with urllib.request.urlopen(request, timeout=300) as response:
        for raw in response:
            line = raw.decode("utf-8").strip()
            if line.startswith("data: "):
                events.append(json.loads(line[6:]))
    return events


def summarize(events: list[dict]) -> dict:
    """Pull out what the CLI shows: replies and full transcripts (in order), tools used, a pending confirmation,
    an upload URL, errors."""
    result = {"replies": [], "transcripts": [], "items": [], "tools": [], "confirmation": None, "upload_url": None,
              "errors": []}
    for event in events:
        if event.get("error"):
            result["errors"].append(event["error"])
        elif event.get("type") == "confirmation_required":
            result["confirmation"] = event.get("question")
        elif event.get("type") == "transcript":
            result["transcripts"].append(event)
            result["items"].append(("transcript", event))
        elif event.get("type") == "ai":
            if event.get("text"):
                result["replies"].append(event["text"])
                result["items"].append(("reply", event["text"]))
            result["tools"] += [call["name"] for call in event.get("tool_calls", [])]
        elif event.get("type") == "tool" and event.get("tool") == "request_audio_upload":
            try:
                result["upload_url"] = json.loads(event["text"]).get("upload_url")
            except (ValueError, AttributeError):
                pass
    return result


def put_file(url: str, path: str) -> int:
    """Upload a recording to the presigned S3 URL the agent handed out, streamed from disk; returns the HTTP status."""
    size = os.path.getsize(path)
    with open(path, "rb") as f:
        request = urllib.request.Request(url, data=f, method="PUT", headers={"Content-Length": str(size)})
        with urllib.request.urlopen(request, timeout=600) as response:
            return response.status
