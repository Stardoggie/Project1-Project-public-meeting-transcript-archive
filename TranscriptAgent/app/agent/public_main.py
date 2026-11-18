"""The public agent (the `public_agent` runtime) behind the web page: it can only read the archive.

Its conversations are kept in the process's own memory, never in the AgentCore Memory the clerks' agent uses, so a
visitor can't open a clerk's conversation by sending its session id. AgentCore keeps each session on its own
instance while it's in use, so a conversation lasts until it has been idle for a while.
"""
from langgraph.checkpoint.memory import InMemorySaver
from graph import build_read_only_graph
from model.load import load_model
from serve import create_app
graph = build_read_only_graph(load_model(), InMemorySaver())
app = create_app(graph)
if __name__ == "__main__":
    app.run()
