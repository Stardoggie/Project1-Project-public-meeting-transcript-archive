import logging
import os
import uuid
from bedrock_agentcore.runtime import BedrockAgentCoreApp
from langchain_core.messages import HumanMessage
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.types import Command
from graph import build_graph, close_dangling_tool_calls
from model.load import load_model
from parse import parse_payload
MEMORY_ID = os.getenv("MEMORY_AGENTMEMORY_ID")
# Tool results are only a peek at what a tool returned; the agent's own replies are never shortened
MAX_TOOL_EVENT_CHARS = 2000
# Clients read these tool results whole: the upload link carries temporary credentials and can pass 2,000 characters
UNTRIMMED_TOOLS = {"request_audio_upload"}


def _make_checkpointer():
    """AgentCore Memory when deployed (set by agentcore deploy), in-process memory for local dev."""
    if MEMORY_ID:
        from langgraph_checkpoint_aws import AgentCoreMemorySaver
        return AgentCoreMemorySaver(MEMORY_ID, region_name=os.getenv("AWS_REGION"))
    return InMemorySaver()


graph = build_graph(load_model(), _make_checkpointer())
log = logging.getLogger("bedrock_agentcore.app")  # the AgentCore app's own logger


def _event(node: str, message) -> dict:
    """A compact, JSON-safe view of one message produced by a graph node."""
    trim = message.type == "tool" and message.name not in UNTRIMMED_TOOLS
    text = message.text[:MAX_TOOL_EVENT_CHARS] if trim else message.text
    event = {"node": node, "type": message.type, "text": text}
    if getattr(message, "tool_calls", None):
        event["tool_calls"] = [{"name": call["name"], "args": call["args"]} for call in message.tool_calls]
    if message.type == "tool":
        event["tool"] = message.name
    return event


def _transcript_event(node: str, message) -> dict | None:
    """The full transcript show_full_transcript attached for the user; the model never sees this text."""
    artifact = getattr(message, "artifact", None)
    if message.type == "tool" and message.name == "show_full_transcript" and isinstance(artifact, dict):
        return {"node": node, "type": "transcript", **artifact}
    return None


def _session_id(context) -> str:
    """The AgentCore session id; a request without one gets a fresh id, so header-less callers never share a
    conversation (or answer each other's confirmations)."""
    return getattr(context, "session_id", None) or f"local-{uuid.uuid4().hex}"


async def handle_request(payload, context, graph=graph):
    """One request; a header-less one is a one-off conversation, so its checkpoints are dropped afterwards."""
    session_id = _session_id(context)
    try:
        async for event in run_turn(payload, session_id, graph=graph):
            yield event
    finally:
        if not getattr(context, "session_id", None):
            try:
                await graph.checkpointer.adelete_thread(session_id)
            except Exception:
                log.warning("Couldn't drop the one-off session %s", session_id, exc_info=True)


def _answer_to_confirmation(payload: dict, prompt: str):
    """What resumes a paused confirmation: an explicit "confirm": true/false, or else the typed message.
    Returns (graph input, error); exactly one of them is None."""
    answer = payload.get("confirm", prompt)
    if answer is None or (isinstance(answer, str) and not answer.strip()):
        return None, "a confirmation is waiting; send 'confirm' (true/false) or a 'prompt' reply"
    return Command(resume=answer), None


def _new_message(payload: dict, prompt: str, state):
    """The graph input for a new message when no confirmation is waiting.
    Returns (graph input, error); exactly one of them is None."""
    if not prompt.strip():
        if "confirm" in payload:
            return None, "there is no confirmation waiting in this session"
        return None, "payload must be a JSON object with a non-empty string 'prompt'"
    # Close tool calls a cut-off request left unanswered, or Bedrock rejects the history from now on
    dangling = close_dangling_tool_calls(state.values.get("messages", []))
    return {"messages": [*dangling, HumanMessage(prompt)]}, None


def _events_from(update: dict):
    """The events a client sees for one step of the graph: messages, full transcripts and confirmation questions."""
    for node, data in update.items():
        if node == "__interrupt__":
            for item in data:
                yield {"node": "confirm", "type": "confirmation_required", **item.value}
            continue
        for message in (data or {}).get("messages", []):
            yield _event(node, message)
            transcript = _transcript_event(node, message)
            if transcript:
                yield transcript


async def run_turn(payload, session_id: str, graph=graph):
    """Stream the events of one request in a session."""
    try:
        prompt, actor_id = parse_payload(payload)
    except ValueError as e:
        yield {"error": str(e)}
        return
    log.info(f"Invoking with session_id={session_id} and actor_id={actor_id}")
    config = {"configurable": {"thread_id": session_id, "actor_id": actor_id}, "recursion_limit": 50}
    state = await graph.aget_state(config)
    if state.interrupts:
        graph_input, error = _answer_to_confirmation(payload, prompt)
    else:
        graph_input, error = _new_message(payload, prompt, state)
    if error:
        yield {"error": error}
        return
    async for update in graph.astream(graph_input, config, stream_mode="updates"):
        for event in _events_from(update):
            yield event


def create_app():
    app = BedrockAgentCoreApp()
    if logging.getLogger().handlers:
        # The root logger already prints (OpenTelemetry's handler when deployed), so the app's own handler would
        # print every line twice
        app.logger.handlers.clear()

    @app.entrypoint
    async def invoke(payload, context):
        async for event in handle_request(payload, context):
            yield event

    return app


app = create_app()
if __name__ == "__main__":
    app.run()
