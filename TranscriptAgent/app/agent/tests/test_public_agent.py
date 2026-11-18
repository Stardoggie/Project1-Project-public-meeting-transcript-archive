"""The public agent: it can only read the archive, whatever tool the model tries to call."""
import asyncio
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
from langgraph.checkpoint.memory import InMemorySaver
import transcript_tools as t
from graph import build_read_only_graph
from test_graph import ScriptedModel, call, new_body
READ_TOOL_NAMES = {
    "list_governing_bodies", "get_governing_body", "search_meetings", "get_meeting",
    "get_transcript", "show_full_transcript", "get_trending_entities"
}


def public_graph(*replies):
    model = ScriptedModel(responses=list(replies))
    model.seen = []
    return build_read_only_graph(model, InMemorySaver()), model


def run(graph, text, thread="public-1"):
    config = {"configurable": {"thread_id": thread}}
    return asyncio.run(graph.ainvoke({"messages": [HumanMessage(text)]}, config))


def tool_names(graph):
    return set(graph.nodes["tools"].bound.tools_by_name)


def test_the_public_agent_has_only_the_read_tools():
    graph, _ = public_graph(AIMessage(content="Hi"))
    assert tool_names(graph) == READ_TOOL_NAMES
    assert {tool.name for tool in t.READ_TOOLS} == READ_TOOL_NAMES


def test_a_read_tool_answers_a_question():
    body = new_body()
    graph, _ = public_graph(AIMessage(content="", tool_calls=[call("get_governing_body", {"body_id": body["id"]})]),
                            AIMessage(content="That's the Graph Test Council."))
    result = run(graph, "tell me about that council")
    tool_message = next(m for m in result["messages"] if isinstance(m, ToolMessage))
    assert "Graph Test Council" in tool_message.content
    assert result["messages"][-1].content == "That's the Graph Test Council."


def test_a_delete_the_model_asks_for_never_runs():
    body = new_body()
    graph, _ = public_graph(AIMessage(content="", tool_calls=[call("delete_governing_body", {"body_id": body["id"]})]),
                            AIMessage(content="I can't delete things."))
    result = run(graph, "delete that council")
    tool_message = next(m for m in result["messages"] if isinstance(m, ToolMessage))
    # the tool isn't there, so the model gets an error back and the body is untouched
    assert tool_message.status == "error"
    assert t.get_governing_body.invoke({"body_id": body["id"]}).get("error") is None


def test_the_public_entrypoint_keeps_conversations_in_its_own_memory(monkeypatch):
    # even where AgentCore Memory is set, which the clerks' agent uses
    monkeypatch.setenv("MEMORY_AGENTMEMORY_ID", "clerks-memory")
    import public_main
    assert isinstance(public_main.graph.checkpointer, InMemorySaver)
    assert tool_names(public_main.graph) == READ_TOOL_NAMES
