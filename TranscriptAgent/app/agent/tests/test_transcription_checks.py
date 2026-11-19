"""The terminal chat's transcription checks: answered without the model. (The public chatbot is a separate runtime
and refuses them; see app/chatbot/tests.)"""
import asyncio
import main
import transcript_tools as t
from test_graph import make_graph, new_body


class FakeCheck:
    """Stands in for the check_transcription tool, so no Transcribe call is made; records the ids it was asked for."""

    def __init__(self, result):
        self.result, self.asked = result, []

    def invoke(self, args):
        self.asked.append(args["meeting_id"])
        return dict(self.result)


class Context:
    session_id = "cli-checks-0123456789abcdef0123456789"


def events_for(payload, graph):
    async def collect():
        return [event async for event in main.handle_request(payload, Context(), graph=graph)]
    return asyncio.run(collect())


def test_the_clerks_agent_answers_a_check_without_the_model(monkeypatch):
    check = FakeCheck({"job_name": "job-7", "status": "COMPLETED", "transcript": "Call to order."})
    monkeypatch.setattr(main, "check_transcription", check)
    # the scripted model has no replies, so any model call would fail the test
    graph, model = make_graph()
    events = events_for({"check_transcription": 7}, graph)
    assert events == [{"type": "transcription_status", "meeting_id": 7, "job_name": "job-7", "status": "COMPLETED"}]
    assert check.asked == [7] and model.seen == []


def test_a_check_needs_a_real_meeting_id(monkeypatch):
    check = FakeCheck({"status": "COMPLETED"})
    monkeypatch.setattr(main, "check_transcription", check)
    graph, _ = make_graph()
    events = events_for({"check_transcription": "seven"}, graph)
    assert events[0]["error"] and check.asked == []


def test_a_started_transcription_says_which_meeting(monkeypatch):
    body = new_body()
    meeting = t.create_meeting.invoke({"body_id": body["id"], "title": "Check Test Meeting", "meeting_date": "2026-11-18"})
    monkeypatch.setattr(t.transcription, "start_transcription_job", lambda meeting_id: {"job_name": "job", "status": "IN_PROGRESS"})
    assert t.start_transcription.invoke({"meeting_id": meeting["id"]})["meeting_id"] == meeting["id"]
    monkeypatch.setattr(t.transcription, "start_transcription_job", lambda meeting_id: {"error": "no_recording"})
    assert "meeting_id" not in t.start_transcription.invoke({"meeting_id": meeting["id"]})
