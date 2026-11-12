import pytest
from pydantic import ValidationError
from meeting_transcript.analysis.store import get_meeting_topic
from meeting_transcript.meetings import store


def test_create_meeting(governing_body):
    created = store.create_meeting(governing_body.id,{
        "title": "City Council Meeting",
        "meeting_date": "2026-09-03"
    })
    assert created.title == "City Council Meeting"
    assert created.status == "scheduled"

@pytest.mark.parametrize(
    "payload",
    [
        {},
        {"title": ""},
        {"title": None},
        {"meeting_date": "not-a-date"}
    ]
)
def test_create_meeting_invalid_input(governing_body, payload):
    with pytest.raises(ValidationError):
        store.create_meeting(governing_body.id,payload)

def test_meetings_filter_by_key_phrase(meeting, governing_body):
    assert get_meeting_topic(body_id=governing_body.id,phrase="budget") == []

def test_delete_meeting_not_found(app):
    assert store.delete_meeting(999999) is False
