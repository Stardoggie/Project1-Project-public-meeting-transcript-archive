"""Tools that run the Public Meeting Transcript Archive's store and service code directly."""
import functools
import logging
import re
import uuid
from typing import Annotated, Any
from langchain_core.tools import tool as _lc_tool
from pydantic import BaseModel, WithJsonSchema
from sqlalchemy import select
from sqlalchemy.exc import DataError, IntegrityError, SQLAlchemyError
from meeting_transcript.app import create_app
from meeting_transcript.aws.aws import get_s3_client
from meeting_transcript.config import BUCKET_NAME
from meeting_transcript.extensions import db
from meeting_transcript.governing_body import store as bodies
from meeting_transcript.governing_body.models_db import GoverningBody
from meeting_transcript.meetings import store as meetings
from meeting_transcript.meetings.models_db import Meeting
from meeting_transcript.analysis import store as analysis
from meeting_transcript.transcription import store as transcripts
from meeting_transcript.transcription import service as transcription
MAX_TRANSCRIPT_CHARS = 40_000
UPLOAD_URL_SECONDS = 3600
# The only key shape request_audio_upload hands out
AUDIO_KEY_PATTERN = re.compile(r"audio/meeting-transcription-[0-9a-f]{32}\.(mp3|wav)")
# Postgres's code for a foreign key violation: a missing referenced row, or a delete of a row still referenced
FOREIGN_KEY_VIOLATION = "23503"
# Ids are Postgres integers, so a bigger one can't name a row
MAX_ID = 2**31 - 1
# An id written as text: ASCII digits only, since str.isdigit() also accepts "²", which int() rejects
ID_DIGITS = re.compile(r"\s*\d+\s*", re.ASCII)
# What a delete tool says when the model calls it directly; deletes only run from the app's confirmation step
DELETE_NOT_RUN = {"error": "not_run", "detail": "Nothing was deleted. Deletes only run through the app's own "
                  "confirmation prompt, which the app shows by itself; the user's reply in chat can't authorise one."}
log = logging.getLogger(__name__)
_flask = create_app()  # only used for its database context
# Send each docstring's Args section to the model as per-argument descriptions
tool = functools.partial(_lc_tool, parse_docstring=True, error_on_invalid_docstring=False)


def _dump(value):
    if hasattr(value, "model_dump"):
        return value.model_dump(mode="json")
    if isinstance(value, list):
        return [_dump(v) for v in value]
    return value


def _database_error(e: SQLAlchemyError) -> str:
    """A short message for a database error; the full one names tables, constraints and SQL, so it stays in the log."""
    if isinstance(e, IntegrityError):
        if getattr(e.orig, "sqlstate", None) == FOREIGN_KEY_VIOLATION:
            if str(e.orig).startswith("update or delete"):
                return "Something was added to it while it was being deleted, so nothing was deleted; try again."
            return "A referenced record doesn't exist."
        return "That conflicts with existing data."
    if isinstance(e, DataError):
        return "A value is the wrong type, format or length."
    return "The archive's database couldn't complete that request."


def _run(fn, *args, **kwargs):
    """Call a store/service function inside the Flask app context; errors come back as data."""
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
    """The result as is, or a not-found error when a store function returned None."""
    if result is None:
        return {"error": "not_found", "detail": f"{what} not found"}
    return result


def _nothing_deleted(what: str) -> dict:
    return {"error": "not_found", "detail": f"{what} not found; nothing was deleted"}


def _meeting_summary(meeting_id: int) -> dict | None:
    """Title, date and governing body of a meeting, or None if there's no such meeting."""
    record = db.session.get(Meeting, meeting_id)
    if record is None:
        return None
    return {"id": record.id, "title": record.title, "meeting_date": record.meeting_date.isoformat(),
            "governing_body": record.body.name}


def _body_summary(body_id: int) -> dict | None:
    """Name and meetings of a governing body, or None if there's no such body."""
    record = db.session.get(GoverningBody, body_id)
    if record is None:
        return None
    rows = db.session.execute(
        select(Meeting.id, Meeting.title, Meeting.meeting_date).where(Meeting.body_id == body_id).order_by(Meeting.id)
    )
    found = [
        {"id": meeting_id, "title": title, "meeting_date": meeting_date.isoformat()}
        for meeting_id, title, meeting_date in rows
    ]
    return {"id": record.id, "name": record.name, "meeting_count": len(found), "meetings": found}


def _body_exists(body_id: int) -> bool:
    return db.session.get(GoverningBody, body_id) is not None


def as_id(value) -> int | None:
    """A well-formed record id: a positive int, or one written as ASCII digits; None for anything else, never an error."""
    if isinstance(value, str) and ID_DIGITS.fullmatch(value):
        value = int(value)
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        return None
    return value


def _lookup(summarise, record_id) -> tuple[str, dict | None]:
    """("found", summary); ("missing", None) for a well-formed id with no record; ("invalid", None) for a malformed
    id; ("unknown", None) if the lookup failed, so whether it exists isn't known."""
    record_id = as_id(record_id)
    if record_id is None:
        return "invalid", None
    if record_id > MAX_ID:
        return "missing", None
    summary = _run(summarise, record_id)
    if summary is None:
        return "missing", None
    if not isinstance(summary, dict) or "error" in summary:
        return "unknown", None
    return "found", summary


def lookup_meeting(meeting_id) -> tuple[str, dict | None]:
    """A meeting's title, date and governing body for a confirmation (not a tool); see _lookup."""
    return _lookup(_meeting_summary, meeting_id)


def lookup_body(body_id) -> tuple[str, dict | None]:
    """A governing body's name and meetings for a confirmation (not a tool); see _lookup."""
    return _lookup(_body_summary, body_id)


def _check_body(body_id):
    """A not-found error for a governing body id that doesn't exist, otherwise None."""
    exists = _run(_body_exists, body_id)
    if isinstance(exists, dict):
        return exists  # the lookup itself failed; pass its error on
    if exists:
        return None
    return {"error": "not_found", "detail": "governing body not found"}


def _delete_body(body_id: int) -> dict | None:
    summary = _body_summary(body_id)
    if summary is None or not bodies.delete_body(body_id):
        return None
    return {"deleted": True, "governing_body": {"id": summary["id"], "name": summary["name"]},
            "meetings_deleted": summary["meetings"]}


def _delete_meeting(meeting_id: int) -> dict | None:
    summary = _meeting_summary(meeting_id)
    if summary is None or not meetings.delete_meeting(meeting_id=meeting_id):
        return None
    return {"deleted": True, "meeting": summary}


def _invalid_id(what: str) -> dict:
    return {"error": "invalid_id", "detail": f"Not a valid {what} id (ids are whole numbers); nothing was deleted."}


def _model_delete(lookup, record_id, what: str) -> dict:
    """What a delete tool tells the model: not found, invalid id, or that only the app's prompt runs deletes."""
    found, _ = lookup(record_id)
    if found == "missing":
        return _nothing_deleted(what)
    if found == "invalid":
        return _invalid_id(what)
    return DELETE_NOT_RUN


def _confirmed_delete(delete, record_id, what: str) -> dict:
    record_id = as_id(record_id)
    if record_id is None:
        return _invalid_id(what)
    if record_id > MAX_ID:
        return _nothing_deleted(what)
    return _not_found(_run(delete, record_id), what)


def delete_body_confirmed(body_id) -> dict:
    """Delete a governing body the user confirmed in the app (not a tool, so the model can't call it)."""
    return _confirmed_delete(_delete_body, body_id, "governing body")


def delete_meeting_confirmed(meeting_id) -> dict:
    """Delete a meeting the user confirmed in the app (not a tool, so the model can't call it)."""
    return _confirmed_delete(_delete_meeting, meeting_id, "meeting")


# --- governing bodies ---

@tool
def list_governing_bodies() -> list:
    """List all governing bodies with their ids and meeting counts."""
    return _run(bodies.list_bodies)


@tool
def get_governing_body(body_id: int) -> dict:
    """Get one governing body by id."""
    return _not_found(_run(bodies.list_body, body_id=body_id), "governing body")


@tool
def create_governing_body(name: str, body: str, description: str) -> dict:
    """Create a governing body.

    Args:
        name: Up to 60 characters.
        body: One of "council", "commission", "board".
        description: At least 5 characters.
    """
    return _run(bodies.create_body, {"name": name, "body": body, "description": description})


@tool
def update_governing_body(body_id: int, name: str, body: str, description: str) -> dict:
    """Replace a governing body's name, type ("council", "commission", "board") and description."""
    result = _run(bodies.update_body, body_id, {"name": name, "body": body, "description": description})
    return _not_found(result, "governing body")


def _raw_id(description: str):
    # Shown to the model as an integer, but passed through as sent, so 8.0 or "²" is reported as an invalid id
    # instead of being coerced into a real one
    return Annotated[Any, WithJsonSchema({"type": "integer", "description": description})]


class _DeleteBodyArgs(BaseModel):
    body_id: _raw_id("Governing body id.")


class _DeleteMeetingArgs(BaseModel):
    meeting_id: _raw_id("Meeting id.")


@_lc_tool(args_schema=_DeleteBodyArgs)  # its own schema, so the docstring is only the description
def delete_governing_body(body_id: int) -> dict:
    """Delete a governing body AND all of its meetings, transcripts, entities and key phrases from the archive.

    The recording files in S3 and past Transcribe jobs are kept; only the archive's records are deleted.
    Calling this makes the app show the user exactly what will be removed and ask them to confirm; it runs only if
    they say yes to that prompt.
    """
    return _model_delete(lookup_body, body_id, "governing body")


# --- meetings ---

@tool
def search_meetings(
    body_id: int,
    key_phrase: str | None = None,
    status: str | None = None,
    start_date: str | None = None,
    end_date: str | None = None
) -> list | dict:
    """Find meetings of a governing body.

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
    """Get one meeting: title, date, status, whether a transcript exists, key phrases and entities."""
    return _not_found(_run(meetings.list_meeting, meeting_id), "meeting")


@tool
def create_meeting(body_id: int, title: str, meeting_date: str) -> dict:
    """Create a scheduled meeting for a governing body. meeting_date is YYYY-MM-DD; title up to 60 characters."""
    return _run(meetings.create_meeting, body_id, {"title": title, "meeting_date": meeting_date})


@tool
def update_meeting(meeting_id: int, title: str, meeting_date: str) -> dict:
    """Change a meeting's title and date (YYYY-MM-DD). Does not re-run transcription."""
    result = _run(meetings.edit_meeting, meeting_id=meeting_id, meeting={"title": title, "meeting_date": meeting_date})
    return _not_found(result, "meeting")


@_lc_tool(args_schema=_DeleteMeetingArgs)
def delete_meeting(meeting_id: int) -> dict:
    """Delete a meeting and its transcript and analysis from the archive.

    The recording file in S3 and past Transcribe jobs are kept; only the archive's records are deleted.
    Calling this makes the app show the user exactly what will be removed and ask them to confirm; it runs only if
    they say yes to that prompt.
    """
    return _model_delete(lookup_meeting, meeting_id, "meeting")


# --- audio and transcription ---

@tool
def request_audio_upload(filename: str) -> dict:
    """Get a one-hour URL for a meeting recording (MP3 or WAV, max 500 MB); the user's app uploads the file to it.

    Don't show the URL or upload instructions to the user. After the app says the upload is done, call
    attach_uploaded_audio with the returned audio_key.
    When deployed, the URL is signed with the runtime's temporary credentials and may expire before
    the hour is up; if an upload is rejected as expired, request a new URL.
    """
    extension = filename.rsplit(".", 1)[-1].lower()
    if extension not in transcription.ALLOWED_AUDIO_EXTENSIONS:
        return {"error": "unsupported_media_type", "detail": "Only MP3 and WAV are accepted."}
    audio_key = f"audio/meeting-transcription-{uuid.uuid4().hex}.{extension}"
    url = get_s3_client().generate_presigned_url(
        "put_object", Params={"Bucket": BUCKET_NAME, "Key": audio_key}, ExpiresIn=UPLOAD_URL_SECONDS
    )
    return {"upload_url": url, "audio_key": audio_key, "method": "PUT"}


@tool
def attach_uploaded_audio(body_id: int, meeting_id: int, audio_key: str) -> dict:
    """Attach an uploaded recording (from request_audio_upload) to a meeting and mark it recorded.

    This replaces any earlier recording and clears that meeting's transcript, entities and key phrases.
    If the meeting already has a transcript, the app asks the user to confirm before this runs.
    """
    if not AUDIO_KEY_PATTERN.fullmatch(audio_key):
        return {"error": "invalid_audio_key", "detail": "Use the audio_key returned by request_audio_upload."}
    try:
        size = get_s3_client().head_object(Bucket=BUCKET_NAME, Key=audio_key)["ContentLength"]
    except Exception:
        return {"error": "upload_not_found", "detail": "The file has not been uploaded yet."}
    if size == 0 or size > transcription.MAX_AUDIO_BYTES:
        return {"error": "invalid_size", "detail": f"File is {size} bytes; must be 1 byte to 500 MB."}
    result = _run(transcripts.attach_audio, audio_key, meeting_id=meeting_id, body_id=body_id)
    if result == {"out": "out_of_bounds"}:
        return {"error": "wrong_governing_body", "detail": "Meeting is not in that governing body."}
    return _not_found(result, "meeting")


@tool
def start_transcription(meeting_id: int) -> dict:
    """Start transcribing a meeting's recording. Transcription takes minutes; check it with check_transcription."""
    return _run(transcription.start_transcription_job, meeting_id)


@tool
def check_transcription(meeting_id: int) -> dict:
    """Check a transcription job. When COMPLETED, this stores the transcript and runs entity and key-phrase analysis."""
    result = _run(transcription.get_transcription_job, meeting_id)
    if isinstance(result, dict) and len(result.get("transcript", "")) > 2000:
        result["transcript"] = result["transcript"][:2000] + "…"  # full text via get_transcript
    return result


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
    shown = {
        "meeting_id": meeting_id,
        "title": meeting["title"],
        "meeting_date": meeting["meeting_date"],
        "transcript": text
    }
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


def meeting_has_transcript(meeting_id) -> bool:
    """True if the meeting may already have a transcript (not a tool; used for confirmation routing). A lookup that
    fails counts as yes, so replacing audio is never let through unasked because of an error."""
    meeting_id = as_id(meeting_id)
    if meeting_id is None or meeting_id > MAX_ID:
        return False  # can't name a meeting, so the tool attaches nothing
    meeting = _run(meetings.list_meeting, meeting_id)
    if meeting is None:
        return False  # no such meeting
    if not isinstance(meeting, dict) or "error" in meeting:
        return True  # the lookup failed, so play it safe and ask
    return bool(meeting.get("transcript_available"))


TOOLS = [
    list_governing_bodies, get_governing_body, create_governing_body, update_governing_body, delete_governing_body,
    search_meetings, get_meeting, create_meeting, update_meeting, delete_meeting,
    request_audio_upload, attach_uploaded_audio, start_transcription, check_transcription,
    get_transcript, show_full_transcript, get_trending_entities
]
