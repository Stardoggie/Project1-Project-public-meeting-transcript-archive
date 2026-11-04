import os

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


def create_app():
    app = BedrockAgentCoreApp()
    log = app.logger

    @app.entrypoint
    async def invoke(payload, context):
        session_id = getattr(context, "session_id", None) or "default-session"
        try:
            prompt, actor_id = parse_payload(payload)
        except ValueError as e:
            yield {"error": str(e)}
            return
        if not isinstance(prompt, str):
            yield {"error": "send a 'prompt' string; the conversation history is kept per session"}
            return

        log.info(f"Invoking with session_id={session_id} and actor_id={actor_id}")
        config = {"configurable": {"thread_id": session_id, "actor_id": actor_id}, "recursion_limit": 50}

        state = await graph.aget_state(config)
        if state.interrupts:
            # A paused confirmation takes an explicit "confirm": true/false, or this message, as its answer
            answer = payload.get("confirm", prompt)
            if answer is None or (isinstance(answer, str) and not answer.strip()):
                yield {"error": "a confirmation is waiting; send 'confirm' (true/false) or a 'prompt' reply"}
                return
            graph_input = Command(resume=answer)
        else:
            if not prompt.strip():
                if "confirm" in payload:
                    yield {"error": "there is no confirmation waiting in this session"}
                else:
                    yield {"error": "payload must be a JSON object with a non-empty string 'prompt'"}
                return
            # Close tool calls a cut-off request left unanswered, or Bedrock rejects the history from now on
            dangling = close_dangling_tool_calls(state.values.get("messages", []))
            graph_input = {"messages": [*dangling, HumanMessage(prompt)]}

        async for update in graph.astream(graph_input, config, stream_mode="updates"):
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

    return app


app = create_app()
if __name__ == "__main__":
    app.run()
