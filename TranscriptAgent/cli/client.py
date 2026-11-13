"""Talks to the transcript agent: a local `agentcore dev` over plain HTTP (standard library only), or the deployed
AgentCore runtime through boto3, which signs each request with your AWS credentials."""
import json
import os
import subprocess
import uuid
import urllib.error
import urllib.request
from pathlib import Path
# agentcore dev normally uses 8080 but moves to the next free port when 8080 is busy
LOCAL_PORTS = range(8080, 8090)
# The AgentCore project folder (TranscriptAgent/), where `agentcore status` knows what is deployed
PROJECT_DIR = Path(__file__).resolve().parent.parent
# A turn can run several tools, so give the deployed agent as long to answer as the local one
DEPLOYED_READ_TIMEOUT_SECONDS = 300


class DeployedAgentError(Exception):
    """The deployed agent couldn't be called, or it returned an error; the message says which."""


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


def find_runtime_arn() -> str | None:
    """AGENT_RUNTIME_ARN if set, otherwise the deployed runtime that `agentcore status` reports for this project."""
    if os.environ.get("AGENT_RUNTIME_ARN"):
        return os.environ["AGENT_RUNTIME_ARN"]
    try:
        status = subprocess.run(["agentcore", "status", "--json"], cwd=PROJECT_DIR, capture_output=True, text=True, timeout=60)
        resources = json.loads(status.stdout).get("resources", [])
    except (OSError, subprocess.SubprocessError, ValueError):
        return None
    for resource in resources:
        if resource.get("resourceType") == "runtime" and resource.get("deploymentState") == "deployed":
            return resource.get("arn")
    return None


def _events_from_lines(lines) -> list[dict]:
    """The agent's streamed events: every `data: {...}` line of the response."""
    events = []
    for raw in lines:
        line = raw.decode("utf-8").strip() if isinstance(raw, bytes) else raw.strip()
        if line.startswith("data: "):
            events.append(json.loads(line[6:]))
    return events


def invoke_deployed(runtime_arn: str, session_id: str, payload: dict) -> list[dict]:
    """Send one request to the deployed AgentCore runtime and return its streamed events."""
    try:
        import boto3
        from botocore.config import Config
        from botocore.exceptions import BotoCoreError, ClientError
    except ImportError:
        raise DeployedAgentError("deployed mode needs boto3; run the chat with the archive's .venv python, "
                                 "or install boto3") from None
    region = runtime_arn.split(":")[3]  # arn:aws:bedrock-agentcore:<region>:<account>:runtime/<id>
    agentcore = boto3.client("bedrock-agentcore", region_name=region,
                             config=Config(read_timeout=DEPLOYED_READ_TIMEOUT_SECONDS))
    try:
        response = agentcore.invoke_agent_runtime(
            agentRuntimeArn=runtime_arn,
            runtimeSessionId=session_id,
            payload=json.dumps(payload).encode(),
            contentType="application/json",
            accept="text/event-stream"
        )
        return _events_from_lines(response["response"].iter_lines())
    except ClientError as e:
        # AWS answered, but refused or failed: show its own error code and message
        error = e.response.get("Error", {})
        raise DeployedAgentError(f"{error.get('Code', 'error')}: {error.get('Message', e)}") from None
    except BotoCoreError as e:
        # no answer at all: credentials, network or a timeout
        raise DeployedAgentError(str(e)) from None


def invoke(agent_url: str, session_id: str, payload: dict) -> list[dict]:
    """Send one request to the agent and return its streamed events."""
    request = urllib.request.Request(
        agent_url,
        data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json", "X-Amzn-Bedrock-AgentCore-Runtime-Session-Id": session_id}
    )
    with urllib.request.urlopen(request, timeout=300) as response:
        return _events_from_lines(response)


def summarize(events: list[dict]) -> dict:
    """Pull out what the CLI shows: replies and full transcripts (in order), tools used, a pending confirmation,
    an upload URL, errors."""
    result = {
        "replies": [],
        "transcripts": [],
        "items": [],
        "tools": [],
        "confirmation": None,
        "upload_url": None,
        "upload_fields": None,
        "audio_key": None,
        "errors": []
    }
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
                handed_out = json.loads(event["text"])
                result["upload_url"] = handed_out.get("upload_url")
                result["upload_fields"] = handed_out.get("upload_fields")  # set when S3 wants a form upload (POST)
                result["audio_key"] = handed_out.get("audio_key")
            except (ValueError, AttributeError):
                pass
    return result


def _form_parts(fields: dict, path: str, boundary: str) -> tuple[bytes, bytes]:
    """The text that goes before and after the file in a multipart form upload. S3 needs every signed field first and
    the file last."""
    before = b""
    for name, value in fields.items():
        before += f'--{boundary}\r\nContent-Disposition: form-data; name="{name}"\r\n\r\n{value}\r\n'.encode()
    file_name = os.path.basename(path).replace('"', "")
    before += f'--{boundary}\r\nContent-Disposition: form-data; name="file"; filename="{file_name}"\r\n\r\n'.encode()
    after = f"\r\n--{boundary}--\r\n".encode()
    return before, after


def _form_body(before: bytes, path: str, after: bytes):
    """The form upload's body, read from disk a megabyte at a time so a large recording isn't loaded into memory."""
    yield before
    with open(path, "rb") as f:
        while chunk := f.read(1024 * 1024):
            yield chunk
    yield after


def post_file(url: str, fields: dict, path: str) -> int:
    """Upload a recording with the signed S3 form the agent handed out; returns the HTTP status (S3 answers 204).
    S3 checks the size and type against the form's signed limits and refuses the file before storing it."""
    boundary = f"----transcript-archive-{uuid.uuid4().hex}"
    before, after = _form_parts(fields, path, boundary)
    size = len(before) + os.path.getsize(path) + len(after)
    headers = {"Content-Type": f"multipart/form-data; boundary={boundary}", "Content-Length": str(size)}
    request = urllib.request.Request(url, data=_form_body(before, path, after), method="POST", headers=headers)
    with urllib.request.urlopen(request, timeout=600) as response:
        return response.status


def put_file(url: str, path: str) -> int:
    """Upload a recording to the presigned S3 URL the agent handed out, streamed from disk; returns the HTTP status."""
    size = os.path.getsize(path)
    with open(path, "rb") as f:
        request = urllib.request.Request(url, data=f, method="PUT", headers={"Content-Length": str(size)})
        with urllib.request.urlopen(request, timeout=600) as response:
            return response.status
