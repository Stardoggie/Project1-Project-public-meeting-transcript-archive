"""The chatbot's read-only tools, its graph, and that it can't change the archive."""
import asyncio
import psycopg
import pytest
from langchain_core.language_models.fake_chat_models import FakeMessagesListChatModel
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
from langgraph.checkpoint.memory import InMemorySaver
from sqlalchemy import text
import tools as t
from graph import build_graph, close_dangling_tool_calls
from meeting_transcript.app import ReadOnlyError
from meeting_transcript.extensions import db
from meeting_transcript.governing_body.models_db import GoverningBody


class ScriptedModel(FakeMessagesListChatModel):
    """Replies with the given messages in order."""

    def bind_tools(self, tools, **kwargs):
        return self


def call(name, args, call_id="call-1"):
    return {"name": name, "args": args, "id": call_id, "type": "tool_call"}


# --- what the chatbot can do ---

def test_only_read_tools_are_offered():
    assert {tool.name for tool in t.TOOLS} == {
        "list_governing_bodies", "get_governing_body", "search_meetings", "get_meeting", "search_transcripts",
        "get_transcript", "show_full_transcript", "get_trending_entities"
    }


def test_read_only_app_refuses_writes(archive):
    with t._flask.app_context():
        db.session.add(GoverningBody(name="Sneaky Board", body="board", description="should never be saved"))
        with pytest.raises(ReadOnlyError, match="INSERT"):
            db.session.commit()
        db.session.rollback()
    names = [body["name"] for body in t.list_governing_bodies.invoke({})]
    assert "Sneaky Board" not in names


@pytest.mark.parametrize("statement", [
    "UPDATE governing_body SET name = 'x'",
    "DELETE FROM meetings",
    "SET default_transaction_read_only = off",
    "SET TRANSACTION READ WRITE",
    "DROP TABLE meetings",
])
def test_read_only_app_only_sends_queries(archive, statement):
    with t._flask.app_context():
        with pytest.raises(ReadOnlyError):
            db.session.execute(text(statement))
        db.session.rollback()


def test_database_refuses_writes_the_app_check_never_sees(archive):
    """A raw connection skips the app's check, and Postgres still refuses the write."""
    with t._flask.app_context():
        raw = db.engine.raw_connection()
        try:
            with raw.cursor() as cur, pytest.raises(psycopg.errors.ReadOnlySqlTransaction):
                cur.execute("INSERT INTO governing_body (name, body, description) VALUES ('Sneaky', 'board', 'x')")
            raw.rollback()
        finally:
            raw.close()


def test_write_errors_are_explained_without_sql(archive):
    def write():
        db.session.add(GoverningBody(name="Sneaky Board", body="board", description="x"))
        db.session.commit()
    result = t._run(write)
    assert result == {"error": "database_error",
                      "detail": "This assistant can only read the archive; it can't change anything."}


# --- tools ---

def test_list_and_get_governing_bodies(archive):
    names = {body["name"] for body in t.list_governing_bodies.invoke({})}
    assert names == {"Springfield City Council", "Parks Board"}
    assert t.get_governing_body.invoke({"body_id": archive["board"]})["name"] == "Parks Board"
    assert t.get_governing_body.invoke({"body_id": 999_999})["error"] == "not_found"


def test_get_meeting_names_its_governing_body(archive):
    meeting = t.get_meeting.invoke({"meeting_id": archive["budget"]})
    assert meeting["title"] == "Budget Hearing"
    assert meeting["governing_body"] == "Springfield City Council"
    assert meeting["transcript_available"] is True
    assert t.get_meeting.invoke({"meeting_id": 999_999})["error"] == "not_found"


def test_search_meetings_by_date(archive):
    found = t.search_meetings.invoke({"body_id": archive["council"], "start_date": "2026-03-15"})
    assert [m["title"] for m in found] == ["Regular Session"]
    assert t.search_meetings.invoke({"body_id": 999_999})["error"] == "not_found"


def test_search_transcripts_across_bodies_newest_first(archive):
    found = t.search_transcripts.invoke({"text": "PARKING"})
    assert [(m["title"], m["governing_body"]) for m in found] == [
        ("Budget Hearing", "Springfield City Council"), ("Parks Review", "Parks Board")
    ]
    assert all(any("parking" in s.lower() for s in m["snippets"]) for m in found)


def test_search_transcripts_in_one_body(archive):
    found = t.search_transcripts.invoke({"text": "parking", "body_id": archive["board"]})
    assert [m["title"] for m in found] == ["Parks Review"]


def test_search_transcripts_matches_wildcards_literally(archive):
    assert [m["title"] for m in t.search_transcripts.invoke({"text": "100%"})] == ["Budget Hearing"]
    assert t.search_transcripts.invoke({"text": "zz%zz"}) == []
    assert t.search_transcripts.invoke({"text": "_"}) == []


def test_search_transcripts_with_nothing_found(archive):
    assert t.search_transcripts.invoke({"text": "zoning variance"}) == []
    assert t.search_transcripts.invoke({"text": "   "}) == []


def test_snippets_are_short_and_marked_when_cut():
    transcript = "a" * 1000 + " parking " + "b" * 1000 + " parking " + "c" * 1000
    from meeting_transcript.meetings.store import MAX_SNIPPETS, SNIPPET_RADIUS, _snippets
    snippets = _snippets(transcript, "Parking")
    assert len(snippets) == 2 <= MAX_SNIPPETS
    assert all(s.startswith("…") and s.endswith("…") and len(s) <= 2 * SNIPPET_RADIUS + 20 for s in snippets)


def test_get_transcript_and_missing_transcript(archive):
    assert "Main Street" in t.get_transcript.invoke({"meeting_id": archive["budget"]})["transcript"]
    assert t.get_transcript.invoke({"meeting_id": archive["planned"]})["error"] == "not_found"


def test_show_full_transcript_goes_to_the_user(archive):
    message = t.show_full_transcript.invoke(call("show_full_transcript", {"meeting_id": archive["budget"]}))
    assert message.artifact["title"] == "Budget Hearing"
    assert "Main Street" in message.artifact["transcript"]
    assert "Main Street" not in message.content


# --- graph ---

def run(graph, text, thread="t1"):
    config = {"configurable": {"thread_id": thread}}
    return asyncio.run(graph.ainvoke({"messages": [HumanMessage(text)]}, config))


def test_graph_answers_after_a_tool_call(archive):
    model = ScriptedModel(responses=[
        AIMessage(content="", tool_calls=[call("search_transcripts", {"text": "parking"})]),
        AIMessage(content="Parking came up at the Budget Hearing (Springfield City Council, 2026-03-04).")
    ])
    result = run(build_graph(model, InMemorySaver()), "What was said about parking?")
    tool_results = [m for m in result["messages"] if isinstance(m, ToolMessage)]
    assert tool_results and "Budget Hearing" in tool_results[0].content
    assert result["messages"][-1].content.startswith("Parking came up")


def test_graph_ends_without_tools():
    result = run(build_graph(ScriptedModel(responses=[AIMessage(content="Hello")]), InMemorySaver()), "hi")
    assert result["messages"][-1].content == "Hello"


def test_close_dangling_tool_calls():
    messages = [HumanMessage("q"), AIMessage(content="", tool_calls=[call("get_meeting", {"meeting_id": 1}, "a"),
                                                                   call("get_meeting", {"meeting_id": 2}, "b")]),
                ToolMessage(content="{}", tool_call_id="a", name="get_meeting")]
    closed = close_dangling_tool_calls(messages)
    assert [m.tool_call_id for m in closed] == ["b"]
    assert close_dangling_tool_calls([HumanMessage("q"), AIMessage(content="done")]) == []
