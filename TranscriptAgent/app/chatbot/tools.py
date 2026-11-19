"""Read-only tools for the public chatbot: they look things up in the archive and never change anything.

The Flask app is created read-only: it only sends SELECT and SHOW, and the database refuses writes on its
connections anyway.
"""
import functools
import logging
from langchain_core.tools import tool as _lc_tool
from sqlalchemy.exc import SQLAlchemyError
from meeting_transcript.app import ReadOnlyError, create_app
from meeting_transcript.extensions import db
from meeting_transcript.governing_body import store as bodies
from meeting_transcript.governing_body.models_db import GoverningBody
from meeting_transcript.meetings import store as meetings
from meeting_transcript.meetings.models_db import Meeting
from meeting_transcript.analysis import store as analysis
from meeting_transcript.transcription import store as transcripts
MAX_TRANSCRIPT_CHARS = 40_000
# Postgres's code for a write attempted in a read-only transaction
READ_ONLY_VIOLATION = "25006"
log = logging.getLogger(__name__)
_flask = create_app(read_only=True)  # only used for its database context
# Send each docstring's Args section to the model as per-argument descriptions
tool = functools.partial(_lc_tool, parse_docstring=True, error_on_invalid_docstring=False)


def _dump(value):
    if hasattr(value, "model_dump"):
        return value.model_dump(mode="json")
    if isinstance(value, list):
        return [_dump(v) for v in value]
    return value


def _database_error(e: SQLAlchemyError) -> str:
    """A short message for a database error; the full one names tables and SQL, so it stays in the log."""
    if isinstance(e, ReadOnlyError) or getattr(getattr(e, "orig", None), "sqlstate", None) == READ_ONLY_VIOLATION:
        return "This assistant can only read the archive; it can't change anything."
    return "The archive's database couldn't complete that request."


def _run(fn, *args, **kwargs):
    """Call a store function inside the Flask app context; errors come back as data."""
    with _flask.app_context():
        try:
            return _dump(fn(*args, **kwargs))
        except SQLAlchemyError as e:
            db.session.rollback()
            log.exception("Database error in %s", getattr(fn, "__name__", fn))
            return {"error": "database_error", "detail": _database_error(e)}
        except Exception as e:
            db.session.rollback()
            return {"error": type(e).__name__, "detail": str(e)[:500]}


def _not_found(result, what: str):
    if result is None:
        return {"error": "not_found", "detail": f"{what} not found"}
    return result


def _body_exists(body_id: int) -> bool:
    return db.session.get(GoverningBody, body_id) is not None


def _check_body(body_id):
    """A not-found error for a governing body id that doesn't exist, otherwise None."""
    exists = _run(_body_exists, body_id)
    if isinstance(exists, dict):
        return exists  # the lookup itself failed; pass its error on
    return None if exists else {"error": "not_found", "detail": "governing body not found"}


def _meeting_with_body(meeting_id: int) -> dict | None:
    """A meeting's details plus the name of its governing body, so answers can say whose meeting it was."""
    found = meetings.list_meeting(meeting_id)
    if found is None:
        return None
    details = found.model_dump(mode="json")
    details["governing_body"] = db.session.get(Meeting, meeting_id).body.name
    return details


# --- governing bodies ---

@tool
def list_governing_bodies() -> list:
    """List all governing bodies (councils, commissions, boards) with their ids and meeting counts."""
    return _run(bodies.list_bodies)


@tool
def get_governing_body(body_id: int) -> dict:
    """Get one governing body by id."""
    return _not_found(_run(bodies.list_body, body_id=body_id), "governing body")


# --- meetings ---

@tool
def search_meetings(
    body_id: int,
    key_phrase: str | None = None,
    status: str | None = None,
    start_date: str | None = None,
    end_date: str | None = None
) -> list | dict:
    """Find meetings of one governing body, with their key phrases and the people, places and organizations named.

    Args:
        body_id: Governing body id.
        key_phrase: Only meetings whose transcript has this key phrase. When set, the other filters are ignored.
        status: One of "scheduled", "recorded", "transcribing", "archived".
        start_date: Earliest meeting date, YYYY-MM-DD.
        end_date: Latest meeting date, YYYY-MM-DD.
    """
    missing = _check_body(body_id)
    if missing:
        return missing
    if key_phrase:
        return _run(analysis.get_meeting_topic, body_id=body_id, phrase=key_phrase)
    return _run(meetings.list_meetings, body_id, status=status, start_date=start_date, end_date=end_date)


@tool
def get_meeting(meeting_id: int) -> dict:
    """Get one meeting: title, date, governing body, status, whether a transcript exists, key phrases and entities."""
    return _not_found(_run(_meeting_with_body, meeting_id), "meeting")


@tool
def search_transcripts(text: str, body_id: int | None = None) -> list | dict:
    """Find meetings whose transcript contains some text (not case-sensitive), newest first, with short excerpts.

    Use this to answer what was said about a subject, or which meetings mentioned a person, place or topic. Try a
    shorter or different wording if nothing matches.

    Args:
        text: The word or phrase to look for, e.g. "parking" or "Main Street".
        body_id: Only search this governing body's meetings.
    """
    if body_id is not None:
        missing = _check_body(body_id)
        if missing:
            return missing
    return _run(meetings.search_transcripts, text, body_id=body_id)


@tool
def get_transcript(meeting_id: int) -> dict:
    """Read a meeting's transcript yourself, to answer questions about it, quote or summarize it.

    Very long transcripts are truncated. When the user just wants to see the full transcript, use
    show_full_transcript instead.
    """
    text = _run(transcripts.get_transcript, meeting_id)
    if text is None:
        return {"error": "not_found", "detail": "No transcript for this meeting."}
    if isinstance(text, str) and len(text) > MAX_TRANSCRIPT_CHARS:
        return {"truncated": True, "transcript": text[:MAX_TRANSCRIPT_CHARS]}
    return text if isinstance(text, dict) else {"transcript": text}


@tool(response_format="content_and_artifact")
def show_full_transcript(meeting_id: int) -> tuple[str, dict | None]:
    """Show the user a meeting's complete transcript, word for word.

    Use this whenever the user wants to see, read or get the full transcript. The text goes straight to the user;
    you only get a short note back, so don't repeat, retype or summarize it unless they ask.
    """
    meeting = _run(meetings.list_meeting, meeting_id)
    if meeting is None:
        return "Meeting not found.", None
    if meeting.get("error"):
        return f"Could not load the meeting: {meeting['detail']}", None
    text = _run(transcripts.get_transcript, meeting_id)
    if not isinstance(text, str):
        return "This meeting has no transcript yet.", None
    shown = {"meeting_id": meeting_id, "title": meeting["title"], "meeting_date": meeting["meeting_date"],
             "transcript": text}
    note = (
        f"The full transcript of '{meeting['title']}' ({meeting['meeting_date']}, {len(text):,} characters) "
        "has been shown to the user directly. Don't repeat or summarize it unless they ask."
    )
    return note, shown


@tool
def get_trending_entities(body_id: int) -> dict:
    """Get the 50 people, places and organizations mentioned most across a governing body's meetings."""
    missing = _check_body(body_id)
    if missing:
        return missing
    return _run(analysis.get_trending_entities, body_id)


TOOLS = [
    list_governing_bodies, get_governing_body, search_meetings, get_meeting, search_transcripts,
    get_transcript, show_full_transcript, get_trending_entities
]
