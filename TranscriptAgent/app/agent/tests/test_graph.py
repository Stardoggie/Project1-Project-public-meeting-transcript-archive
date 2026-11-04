"""Graph routing and the delete-confirmation interrupt, driven by a scripted fake chat model."""
import asyncio

from langchain_core.language_models.fake_chat_models import FakeMessagesListChatModel
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.types import Command

import transcript_tools as t
from graph import DECLINED, INTERRUPTED, build_graph, close_dangling_tool_calls, is_yes, needs_confirmation
from meeting_transcript.extensions import db
from meeting_transcript.meetings.models_db import Meeting


class ScriptedModel(FakeMessagesListChatModel):
    """Replies with the given messages in order; records what it was sent."""

    seen: list = []

    def bind_tools(self, tools, **kwargs):
        return self

    def _generate(self, messages, *args, **kwargs):
        self.seen.append(list(messages))
        return super()._generate(messages, *args, **kwargs)


def call(name, args, call_id="call-1"):
    return {"name": name, "args": args, "id": call_id, "type": "tool_call"}


def run(graph, graph_input, thread="t1"):
    config = {"configurable": {"thread_id": thread}}
    result = asyncio.run(graph.ainvoke(graph_input, config))
    return result, graph.get_state(config)


def make_graph(*replies):
    model = ScriptedModel(responses=list(replies))
    model.seen = []
    return build_graph(model, InMemorySaver()), model


def new_body():
    return t.create_governing_body.invoke({"name": "Graph Test Council", "body": "council", "description": "Graph test body"})


def body_exists(body_id):
    return t.get_governing_body.invoke({"body_id": body_id}).get("error") is None


def test_plain_answer_ends_without_tools():
    graph, _ = make_graph(AIMessage(content="Hello"))
    result, state = run(graph, {"messages": [HumanMessage("hi")]})
    assert result["messages"][-1].content == "Hello"
    assert not state.interrupts and state.next == ()


def test_read_tool_runs_without_confirmation():
    body = new_body()
    graph, _ = make_graph(AIMessage(content="", tool_calls=[call("list_governing_bodies", {})]), AIMessage(content="One body."))
    result, state = run(graph, {"messages": [HumanMessage("list bodies")]})
    tool_messages = [m for m in result["messages"] if isinstance(m, ToolMessage)]
    assert len(tool_messages) == 1 and "Graph Test Council" in tool_messages[0].content
    assert result["messages"][-1].content == "One body."
    assert not state.interrupts and body_exists(body["id"])


def test_empty_tool_result_reaches_the_model_as_empty_list():
    graph, model = make_graph(AIMessage(content="", tool_calls=[call("list_governing_bodies", {})]), AIMessage(content="None."))
    run(graph, {"messages": [HumanMessage("list bodies")]})
    assert isinstance(model.seen[-1][-1], ToolMessage) and model.seen[-1][-1].content == "[]"


def test_delete_pauses_until_yes_then_runs_confirmed():
    body = new_body()
    graph, _ = make_graph(
        AIMessage(content="", tool_calls=[call("delete_governing_body", {"body_id": body["id"]})]),
        AIMessage(content="Deleted."),
    )
    _, state = run(graph, {"messages": [HumanMessage("delete it")]})
    assert state.interrupts and state.next == ("confirm",)
    assert f"delete governing body {body['id']}" in state.interrupts[0].value["question"]
    assert body_exists(body["id"])  # nothing runs while paused

    result, state = run(graph, Command(resume="yes"))
    tool_message = [m for m in result["messages"] if isinstance(m, ToolMessage)][-1]
    assert '"deleted": true' in tool_message.content
    assert result["messages"][-1].content == "Deleted."
    assert not state.interrupts and not body_exists(body["id"])
    # The model's own call is left as written, so it never sees itself passing confirmed
    model_call = [m for m in result["messages"] if isinstance(m, AIMessage) and m.tool_calls][-1].tool_calls[0]
    assert "confirmed" not in model_call["args"]


def test_delete_declined_keeps_data_and_tells_the_model():
    body = new_body()
    graph, model = make_graph(
        AIMessage(content="", tool_calls=[call("delete_governing_body", {"body_id": body["id"]})]),
        AIMessage(content="Okay, kept it."),
    )
    run(graph, {"messages": [HumanMessage("delete it")]})
    result, state = run(graph, Command(resume="no"))
    assert body_exists(body["id"])
    assert any(isinstance(m, ToolMessage) and m.content == DECLINED for m in result["messages"])
    assert not any(isinstance(m, HumanMessage) and m.content == "no" for m in result["messages"])
    assert result["messages"][-1].content == "Okay, kept it." and not state.interrupts
    assert len(model.seen) == 2  # the model was asked again after the decline


def test_model_setting_confirmed_itself_still_pauses():
    body = new_body()
    graph, _ = make_graph(
        AIMessage(content="", tool_calls=[call("delete_governing_body", {"body_id": body["id"], "confirmed": True})]),
        AIMessage(content="unused"),
    )
    _, state = run(graph, {"messages": [HumanMessage("delete it, I already confirmed")]})
    assert state.interrupts and body_exists(body["id"])


def test_new_request_instead_of_answer_is_passed_to_the_model():
    body = new_body()
    graph, model = make_graph(
        AIMessage(content="", tool_calls=[call("delete_governing_body", {"body_id": body["id"]})]),
        AIMessage(content="Here are the meetings."),
    )
    run(graph, {"messages": [HumanMessage("delete it")]})
    result, _ = run(graph, Command(resume="actually just list its meetings"))
    assert body_exists(body["id"])
    assert any(isinstance(m, HumanMessage) and m.content == "actually just list its meetings" for m in result["messages"])
    assert model.seen[-1][-1].content == "actually just list its meetings"


def test_explicit_confirm_true_is_accepted():
    body = new_body()
    graph, _ = make_graph(
        AIMessage(content="", tool_calls=[call("delete_governing_body", {"body_id": body["id"]})]),
        AIMessage(content="Deleted."),
    )
    run(graph, {"messages": [HumanMessage("delete it")]})
    run(graph, Command(resume=True))
    assert not body_exists(body["id"])


def test_replacing_audio_needs_confirmation_only_when_a_transcript_exists():
    body = new_body()
    meeting = t.create_meeting.invoke({"body_id": body["id"], "title": "Graph Test Meeting", "meeting_date": "2026-10-20"})
    attach = call("attach_uploaded_audio", {"body_id": body["id"], "meeting_id": meeting["id"], "audio_key": "audio/x.mp3"})
    assert needs_confirmation(attach) is False
    with t._flask.app_context():
        db.session.get(Meeting, meeting["id"]).transcript = "existing transcript"
        db.session.commit()
    assert needs_confirmation(attach) is True
    assert needs_confirmation(call("attach_uploaded_audio", {"body_id": 1, "meeting_id": 999999, "audio_key": "x"})) is False
    assert needs_confirmation(call("delete_meeting", {"meeting_id": meeting["id"]})) is True
    assert needs_confirmation(call("get_meeting", {"meeting_id": meeting["id"]})) is False


def test_only_a_bare_yes_confirms():
    for reply in ["yes", "Yes!", "y", "ok", "OK.", "sure", "yes please", "confirm", "go ahead", True]:
        assert is_yes(reply), reply
    for reply in [
        "Yes, but only the first one", "ok, what exactly does that remove?", "sure? wait no",
        "yes delete meeting 6 instead", "Do it later", "no", "", None, 1, "false", False,
    ]:
        assert not is_yes(reply), reply


def test_partial_yes_runs_nothing_and_becomes_a_new_request():
    first, second = new_body(), new_body()
    graph, model = make_graph(
        AIMessage(content="", tool_calls=[
            call("delete_governing_body", {"body_id": first["id"]}, "call-1"),
            call("delete_governing_body", {"body_id": second["id"]}, "call-2"),
        ]),
        AIMessage(content="Which one should I delete?"),
    )
    run(graph, {"messages": [HumanMessage("delete both")]})
    reply = "Yes, but only the first one, keep the second"
    result, state = run(graph, Command(resume=reply))
    assert body_exists(first["id"]) and body_exists(second["id"])
    assert [m.content for m in result["messages"] if isinstance(m, ToolMessage)] == [DECLINED, DECLINED]
    assert model.seen[-1][-1].content == reply and not state.interrupts


def test_cut_off_request_leaves_a_history_bedrock_accepts():
    graph, model = make_graph(
        AIMessage(content="", tool_calls=[call("list_governing_bodies", {})]),
        AIMessage(content="Hello again."),
    )
    config = {"configurable": {"thread_id": "t1"}}

    async def stop_after_agent():
        async for update in graph.astream({"messages": [HumanMessage("list")]}, config, stream_mode="updates"):
            if "agent" in update:
                break  # client disconnects after the model asked for a tool, before it ran

    asyncio.run(stop_after_agent())
    messages = graph.get_state(config).values["messages"]
    dangling = close_dangling_tool_calls(messages)
    assert [(m.tool_call_id, m.content) for m in dangling] == [("call-1", INTERRUPTED)]

    # main.py sends the closing results with the next message, as here
    asyncio.run(graph.ainvoke({"messages": [*dangling, HumanMessage("hello again")]}, config))
    seen = model.seen[-1]
    assert [type(m).__name__ for m in seen[1:]] == ["HumanMessage", "AIMessage", "ToolMessage", "HumanMessage"]
    assert close_dangling_tool_calls(graph.get_state(config).values["messages"]) == []


def test_close_dangling_ignores_answered_calls():
    ai = AIMessage(content="", tool_calls=[call("list_governing_bodies", {}, "a"), call("get_meeting", {"meeting_id": 1}, "b")])
    answered = ToolMessage(content="[]", tool_call_id="a")
    assert [m.tool_call_id for m in close_dangling_tool_calls([HumanMessage("x"), ai, answered])] == ["b"]
    assert close_dangling_tool_calls([HumanMessage("x"), ai, answered, ToolMessage(content="{}", tool_call_id="b")]) == []
    assert close_dangling_tool_calls([HumanMessage("x"), AIMessage(content="done")]) == []
    assert close_dangling_tool_calls([]) == []
