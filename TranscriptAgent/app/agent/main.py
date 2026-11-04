import os

from bedrock_agentcore.runtime import BedrockAgentCoreApp
from langchain_core.messages import HumanMessage
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.types import Command

from graph import build_graph
from model.load import load_model
from parse import parse_payload

MEMORY_ID = os.getenv("MEMORY_AGENTMEMORY_ID")
MAX_EVENT_CHARS = 2000


def _make_checkpointer():
    """AgentCore Memory when deployed (set by agentcore deploy), in-process memory for local dev."""
    if MEMORY_ID:
        from langgraph_checkpoint_aws import AgentCoreMemorySaver

        return AgentCoreMemorySaver(MEMORY_ID, region_name=os.getenv("AWS_REGION"))
    return InMemorySaver()


graph = build_graph(load_model(), _make_checkpointer())


def _event(node: str, message) -> dict:
    """A compact, JSON-safe view of one message produced by a graph node."""
    event = {"node": node, "type": message.type, "text": message.text[:MAX_EVENT_CHARS]}
    if getattr(message, "tool_calls", None):
        event["tool_calls"] = [{"name": call["name"], "args": call["args"]} for call in message.tool_calls]
    if message.type == "tool":
        event["tool"] = message.name
    return event


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
        if not isinstance(prompt, str) or not prompt.strip():
            yield {"error": "payload must be a JSON object with a non-empty string 'prompt'"}
            return

        log.info(f"Invoking with session_id={session_id} and actor_id={actor_id}")
        config = {"configurable": {"thread_id": session_id, "actor_id": actor_id}, "recursion_limit": 50}

        # A paused confirmation takes this message (or an explicit "confirm": true/false) as its answer
        state = await graph.aget_state(config)
        if state.interrupts:
            graph_input = Command(resume=payload.get("confirm", prompt))
        else:
            graph_input = {"messages": [HumanMessage(prompt)]}

        async for update in graph.astream(graph_input, config, stream_mode="updates"):
            for node, data in update.items():
                if node == "__interrupt__":
                    for item in data:
                        yield {"node": "confirm", "type": "confirmation_required", **item.value}
                    continue
                for message in (data or {}).get("messages", []):
                    yield _event(node, message)

    return app


app = create_app()
if __name__ == "__main__":
    app.run()
