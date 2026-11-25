"""Archive records for the tests, written through the archive's own store functions."""
from meeting_transcript.governing_body import store as bodies
from meeting_transcript.meetings import store as meetings
from meeting_transcript.extensions import db
from meeting_transcript.meetings.models_db import Entities, KeyPhrases, Meeting
from archive_site.archive import run


def new_body(name="Springfield City Council"):
    return run(bodies.create_body, {"name": name, "body": "council", "description": "City council"})


def new_meeting(body_id, title="Budget Hearing", meeting_date="2026-03-04"):
    return run(meetings.create_meeting, body_id, {"title": title, "meeting_date": meeting_date})


def _store_transcript(meeting_id, transcript, phrases, entities):
    record = db.session.get(Meeting, meeting_id)
    record.transcript = transcript
    record.status = "archived"
    record.keys = [KeyPhrases(phrase=phrase) for phrase in phrases]
    record.entities = [Entities(entity=name, entity_count=count) for name, count in entities.items()]
    db.session.commit()


def give_transcript(meeting_id, transcript, phrases=(), entities=None):
    """As if transcription and analysis had finished for this meeting."""
    run(_store_transcript, meeting_id, transcript, phrases, entities or {})


def meeting_row(meeting_id):
    """The meeting as the archive stores it, or None."""
    return run(lambda: db.session.get(Meeting, meeting_id))
