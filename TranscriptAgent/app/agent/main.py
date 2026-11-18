"""The clerks' agent (the `agent` runtime): every archive tool, with deletes and audio replacement confirmed first."""
import os
from langgraph.checkpoint.memory import InMemorySaver
from graph import build_graph
from model.load import load_model
from serve import create_app
MEMORY_ID = os.getenv("MEMORY_AGENTMEMORY_ID")


def _make_checkpointer():
    """AgentCore Memory when deployed (set by agentcore deploy), in-process memory for local dev."""
    if MEMORY_ID:
        from langgraph_checkpoint_aws import AgentCoreMemorySaver
        return AgentCoreMemorySaver(MEMORY_ID, region_name=os.getenv("AWS_REGION"))
    return InMemorySaver()


graph = build_graph(load_model(), _make_checkpointer())
# direct_checks lets the terminal chat check a transcription without the model while it waits for it
app = create_app(graph, direct_checks=True)
if __name__ == "__main__":
    app.run()
