"""AgentCore entrypoint for the public, read-only chatbot behind the archive's website."""
import logging
import os
import uuid
from bedrock_agentcore.runtime import BedrockAgentCoreApp
from langchain_core.messages import HumanMessage
from langgraph.checkpoint.memory import InMemorySaver
from graph import build_graph, close_dangling_tool_calls
from model.load import load_model
from parse import parse_payload
MEMORY_ID = os.getenv("MEMORY_AGENTMEMORY_ID")
# Tool results are only a peek at what a tool returned; the chatbot's own replies are never shortened
MAX_TOOL_EVENT_CHARS = 2000


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
    text = message.text[:MAX_TOOL_EVENT_CHARS] if message.type == "tool" else message.text
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


def _events_from(update: dict):
    """The events a client sees for one step of the graph: messages and full transcripts."""
    for node, data in update.items():
        for message in (data or {}).get("messages", []):
            yield _event(node, message)
            transcript = _transcript_event(node, message)
            if transcript:
                yield transcript


def _session_id(context) -> str:
    """The AgentCore session id; a request without one gets a fresh id, so header-less callers never share a
    conversation."""
    return getattr(context, "session_id", None) or f"local-{uuid.uuid4().hex}"


async def run_turn(payload, session_id: str, graph=graph):
    """Stream the events of one question in a session."""
    try:
        prompt, actor_id = parse_payload(payload)
    except ValueError as e:
        yield {"error": str(e)}
        return
    if not prompt.strip():
        yield {"error": "payload must be a JSON object with a non-empty string 'prompt'"}
        return
    log.info(f"Invoking with session_id={session_id} and actor_id={actor_id}")
    config = {"configurable": {"thread_id": session_id, "actor_id": actor_id}, "recursion_limit": 30}
    state = await graph.aget_state(config)
    # Close tool calls a cut-off request left unanswered, or Bedrock rejects the history from now on
    dangling = close_dangling_tool_calls(state.values.get("messages", []))
    async for update in graph.astream({"messages": [*dangling, HumanMessage(prompt)]}, config, stream_mode="updates"):
        for event in _events_from(update):
            yield event


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
