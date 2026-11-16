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
    assert f'delete governing body "Graph Test Council" (id {body["id"]}), which has no meetings.' in state.interrupts[0].value["question"]
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


def test_only_a_plain_yes_confirms():
    for reply in ["yes", "YES", "Yes!", "yes.", "  yes  ", True]:
        assert is_yes(reply), reply
    for reply in [
        "y", "ok", "OK.", "okay", "sure", "do it", "go ahead", "confirm", "confirmed", "proceed", "yeah", "yep",
        "yes please", "Yes, but only the first one", "ok, what exactly does that remove?", "sure? wait no",
        "yes delete meeting 6 instead", "Do it later", "yes?", "no", "", None, 1, "false", False,
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


def transcribed_meeting(text):
    body = new_body()
    meeting = t.create_meeting.invoke({"body_id": body["id"], "title": "Long Hearing", "meeting_date": "2026-11-10"})
    with t._flask.app_context():
        db.session.get(Meeting, meeting["id"]).transcript = text
        db.session.commit()
    return meeting["id"]


def test_full_transcript_goes_to_the_user_not_the_model():
    text = "Word " * 5000  # 25,000 characters, well past every cap
    meeting_id = transcribed_meeting(text)
    graph, model = make_graph(
        AIMessage(content="", tool_calls=[call("show_full_transcript", {"meeting_id": meeting_id})]),
        AIMessage(content="Here's the full transcript of Long Hearing."),
    )
    result, _ = run(graph, {"messages": [HumanMessage("show me the full transcript")]})
    shown = [m for m in result["messages"] if isinstance(m, ToolMessage) and m.name == "show_full_transcript"][-1]
    assert shown.artifact["transcript"] == text and shown.artifact["title"] == "Long Hearing"
    assert "Word Word" not in shown.content and "shown to the user directly" in shown.content
    # The model only ever saw the short note
    assert all("Word Word" not in str(m.content) for m in model.seen[-1])


def test_full_transcript_without_one_says_so():
    body = new_body()
    meeting = t.create_meeting.invoke({"body_id": body["id"], "title": "Untranscribed", "meeting_date": "2026-11-10"})
    note, shown = t.show_full_transcript.func(meeting["id"])
    assert shown is None and "no transcript" in note
    note, shown = t.show_full_transcript.func(999999)
    assert shown is None and note == "Meeting not found."


def test_replies_are_not_shortened_but_tool_peeks_are():
    import main

    long_reply = "x" * 20_000
    assert main._event("agent", AIMessage(content=long_reply))["text"] == long_reply
    assert len(main._event("tools", ToolMessage(content="y" * 5000, name="get_meeting", tool_call_id="c"))["text"]) == 2000
    shown = ToolMessage(content="note", name="show_full_transcript", tool_call_id="c",
                        artifact={"meeting_id": 1, "title": "T", "meeting_date": "2026-11-10", "transcript": long_reply})
    event = main._transcript_event("tools", shown)
    assert event["type"] == "transcript" and event["transcript"] == long_reply
    assert main._transcript_event("tools", ToolMessage(content="x", name="get_meeting", tool_call_id="c")) is None


def test_upload_links_are_never_trimmed():
    import main

    link = '{"upload_url": "https://s3/' + "x" * 3000 + '", "audio_key": "audio/k.mp3", "method": "PUT"}'
    event = main._event("tools", ToolMessage(content=link, name="request_audio_upload", tool_call_id="c"))
    assert event["text"] == link


def test_deleting_a_missing_id_says_not_found_without_asking():
    for name, args in [("delete_governing_body", {"body_id": 999999}), ("delete_meeting", {"meeting_id": 999999})]:
        assert needs_confirmation(call(name, args)) is False
        graph, _ = make_graph(AIMessage(content="", tool_calls=[call(name, args)]), AIMessage(content="Not found."))
        result, state = run(graph, {"messages": [HumanMessage("delete it")]}, thread=name)
        tool_message = [m for m in result["messages"] if isinstance(m, ToolMessage)][-1]
        assert not state.interrupts and '"not_found"' in tool_message.content


def test_search_and_trending_on_a_missing_body_are_not_found():
    assert t.search_meetings.invoke({"body_id": 999999})["error"] == "not_found"
    assert t.search_meetings.invoke({"body_id": 999999, "key_phrase": "budget"})["error"] == "not_found"
    assert t.get_trending_entities.invoke({"body_id": 999999})["error"] == "not_found"
    body = new_body()
    assert t.search_meetings.invoke({"body_id": body["id"]}) == []


def test_confirmation_names_what_goes_and_the_result_says_what_went():
    body = new_body()
    first = t.create_meeting.invoke({"body_id": body["id"], "title": "Budget Hearing", "meeting_date": "2026-10-20"})
    t.create_meeting.invoke({"body_id": body["id"], "title": "Zoning Review", "meeting_date": "2026-10-27"})
    graph, _ = make_graph(
        AIMessage(content="", tool_calls=[call("delete_meeting", {"meeting_id": first["id"]})]),
        AIMessage(content="Deleted."),
    )
    _, state = run(graph, {"messages": [HumanMessage("delete the budget hearing")]})
    question = state.interrupts[0].value["question"]
    assert f'delete meeting "Budget Hearing" on 2026-10-20 (Graph Test Council, id {first["id"]})' in question

    graph, _ = make_graph(
        AIMessage(content="", tool_calls=[call("delete_governing_body", {"body_id": body["id"]})]),
        AIMessage(content="Deleted."),
    )
    _, state = run(graph, {"messages": [HumanMessage("delete the council")]}, thread="t2")
    assert "and its 2 meetings" in state.interrupts[0].value["question"]
    result, _ = run(graph, Command(resume="yes"), thread="t2")
    deleted = [m for m in result["messages"] if isinstance(m, ToolMessage)][-1].content
    assert '"name": "Graph Test Council"' in deleted and "Budget Hearing" in deleted and "Zoning Review" in deleted


def test_database_errors_reach_the_model_without_sql():
    result = t.create_meeting.invoke({"body_id": 999999, "title": "Orphan", "meeting_date": "2026-10-20"})
    assert result == {"error": "database_error", "detail": "A referenced record doesn't exist."}
    # the app's own validation messages still come through
    assert "meeting_date" in t.create_meeting.invoke({"body_id": 1, "title": "Bad", "meeting_date": "soon"})["detail"]


def test_requests_without_a_session_id_never_share_one():
    import main

    class Context:
        session_id = None

    first, second = main._session_id(Context()), main._session_id(None)
    assert first != second and first.startswith("local-")
    Context.session_id = "cli-abc"
    assert main._session_id(Context()) == "cli-abc"


def test_a_messages_payload_is_told_to_send_a_prompt():
    from parse import parse_payload
    import pytest

    with pytest.raises(ValueError, match="send a 'prompt' string"):
        parse_payload({"messages": []})
    assert parse_payload({"prompt": "hi", "actorId": ""}) == ("hi", "default")


def test_a_string_id_with_confirmed_set_still_asks_first():
    body = new_body()
    t.create_meeting.invoke({"body_id": body["id"], "title": "Kept", "meeting_date": "2026-10-20"})
    graph, _ = make_graph(
        AIMessage(content="", tool_calls=[call("delete_governing_body", {"body_id": str(body["id"]), "confirmed": True})]),
        AIMessage(content="unused"),
    )
    _, state = run(graph, {"messages": [HumanMessage("delete it")]})
    assert state.interrupts and body_exists(body["id"])
    assert "and its 1 meeting," in state.interrupts[0].value["question"]


def test_a_failed_lookup_still_asks_first(monkeypatch):
    from sqlalchemy.exc import OperationalError

    body = new_body()
    meeting = t.create_meeting.invoke({"body_id": body["id"], "title": "Kept", "meeting_date": "2026-10-20"})

    def down(record_id):
        raise OperationalError("SELECT 1", {}, Exception("connection lost"))
    monkeypatch.setattr(t, "_body_summary", down)
    monkeypatch.setattr(t, "_meeting_summary", down)
    monkeypatch.setattr(t.meetings, "list_meeting", down)
    assert needs_confirmation(call("delete_governing_body", {"body_id": body["id"]})) is True
    assert needs_confirmation(call("delete_meeting", {"meeting_id": meeting["id"]})) is True
    attach = call("attach_uploaded_audio", {"body_id": body["id"], "meeting_id": meeting["id"], "audio_key": "x"})
    assert needs_confirmation(attach) is True
    graph, _ = make_graph(AIMessage(content="", tool_calls=[call("delete_governing_body", {"body_id": body["id"]})]))
    _, state = run(graph, {"messages": [HumanMessage("delete it")]})
    assert "couldn't be read" in state.interrupts[0].value["question"]
    monkeypatch.undo()
    assert body_exists(body["id"])


def test_the_model_can_never_run_a_delete_itself():
    body = new_body()
    meeting = t.create_meeting.invoke({"body_id": body["id"], "title": "Kept", "meeting_date": "2026-10-20"})
    assert "confirmed" not in t.delete_governing_body.args and "confirmed" not in t.delete_meeting.args
    for tool, args in [(t.delete_meeting, {"meeting_id": meeting["id"]}), (t.delete_governing_body, {"body_id": body["id"]})]:
        result = tool.invoke({**args, "confirmed": True})
        assert result == t.DELETE_NOT_RUN and "ask" not in result["detail"].lower()
    assert body_exists(body["id"]) and t.get_meeting.invoke({"meeting_id": meeting["id"]}).get("error") is None
    # a yes typed in chat is just a new message: only the app's own prompt runs a delete
    graph, model = make_graph(
        AIMessage(content="", tool_calls=[call("delete_meeting", {"meeting_id": meeting["id"]})]),
        AIMessage(content="", tool_calls=[call("delete_meeting", {"meeting_id": meeting["id"]}, "call-2")]),
        AIMessage(content="unused"),
    )
    run(graph, {"messages": [HumanMessage("delete it")]})
    _, state = run(graph, Command(resume="ok, go"))
    assert state.interrupts and t.get_meeting.invoke({"meeting_id": meeting["id"]}).get("error") is None


def test_malformed_and_out_of_range_ids_never_delete():
    for value in ["abc", -1, 0, True, None, 1.5]:
        assert t.as_id(value) is None, value
    assert t.as_id(" 12 ") == 12 and t.as_id(12) == 12
    assert t.lookup_body(2**40) == ("missing", None)
    assert t.delete_body_confirmed("abc")["error"] == "invalid_id"
    assert t.delete_meeting_confirmed(2**40)["error"] == "not_found"


def test_a_delete_that_loses_a_race_is_explained():
    from sqlalchemy.exc import IntegrityError

    class Orig(Exception):
        sqlstate = t.FOREIGN_KEY_VIOLATION
    deleting = IntegrityError("DELETE", {}, Orig('update or delete on table "meetings" violates foreign key constraint'))
    inserting = IntegrityError("INSERT", {}, Orig('insert or update on table "meetings" violates foreign key constraint'))
    assert "while it was being deleted" in t._database_error(deleting)
    assert t._database_error(inserting) == "A referenced record doesn't exist."


def test_non_ascii_digits_are_invalid_and_never_break_the_turn():
    import asyncio as aio
    from graph import route_after_agent

    for value in ["²", "١٢", "1e3", "8.0", " "]:
        assert t.as_id(value) is None, value
    assert t.lookup_body("²") == ("invalid", None)
    message = AIMessage(content="", tool_calls=[call("delete_governing_body", {"body_id": "²"})])
    assert aio.run(route_after_agent({"messages": [message]})) == "tools"
    assert t.delete_governing_body.invoke({"body_id": "²"})["error"] == "invalid_id"


def test_float_ids_get_a_clear_invalid_id_from_the_delete_tools():
    body = new_body()
    meeting = t.create_meeting.invoke({"body_id": body["id"], "title": "Kept", "meeting_date": "2026-10-20"})
    assert t.delete_meeting.args["meeting_id"]["type"] == "integer"  # still an integer to the model
    for tool, args in [(t.delete_governing_body, {"body_id": float(body["id"])}),
                       (t.delete_meeting, {"meeting_id": float(meeting["id"])})]:
        result = tool.invoke(args)
        assert result["error"] == "invalid_id" and "whole numbers" in result["detail"]
    graph, _ = make_graph(
        AIMessage(content="", tool_calls=[call("delete_meeting", {"meeting_id": float(meeting["id"])})]),
        AIMessage(content="That id isn't valid."),
    )
    result, state = run(graph, {"messages": [HumanMessage("delete it")]})
    assert not state.interrupts and '"invalid_id"' in [m for m in result["messages"] if isinstance(m, ToolMessage)][-1].content
    assert t.get_meeting.invoke({"meeting_id": meeting["id"]}).get("error") is None
