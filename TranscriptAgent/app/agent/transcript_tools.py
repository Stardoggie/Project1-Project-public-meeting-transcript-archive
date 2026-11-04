"""Tools that run the Public Meeting Transcript Archive's store and service code directly."""
import functools
import re
import uuid

from langchain_core.tools import tool as _lc_tool

from meeting_transcript.app import create_app
from meeting_transcript.aws.aws import get_s3_client
from meeting_transcript.config import BUCKET_NAME
from meeting_transcript.extensions import db
from meeting_transcript.governing_body import store as bodies
from meeting_transcript.meetings import store as meetings
from meeting_transcript.analysis import store as analysis
from meeting_transcript.transcription import store as transcripts
from meeting_transcript.transcription import service as transcription

MAX_TRANSCRIPT_CHARS = 40_000
UPLOAD_URL_SECONDS = 3600
# The only key shape request_audio_upload hands out
AUDIO_KEY_PATTERN = re.compile(r"audio/meeting-transcription-[0-9a-f]{32}\.(mp3|wav)")

_flask = create_app()  # only used for its database context

# Send each docstring's Args section to the model as per-argument descriptions
tool = functools.partial(_lc_tool, parse_docstring=True, error_on_invalid_docstring=False)


def _dump(value):
    if hasattr(value, "model_dump"):
        return value.model_dump(mode="json")
    if isinstance(value, list):
        return [_dump(v) for v in value]
    return value


def _run(fn, *args, **kwargs):
    """Call a store/service function inside the Flask app context; errors come back as data."""
    with _flask.app_context():
        try:
            return _dump(fn(*args, **kwargs))
        except Exception as e:
            db.session.rollback()
            return {"error": type(e).__name__, "detail": str(e)[:500]}


def _not_found(result, what: str):
    return {"error": "not_found", "detail": f"{what} not found"} if result is None else result


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


@tool
def delete_governing_body(body_id: int, confirmed: bool = False) -> dict:
    """Delete a governing body AND all of its meetings, transcripts, entities and key phrases.

    The app asks the user to confirm before this runs and sets confirmed itself; leave confirmed unset.
    """
    if not confirmed:
        return {"error": "confirmation_required", "detail": "Ask the user to confirm first."}
    return {"deleted": _run(bodies.delete_body, body_id)}


# --- meetings ---

@tool
def search_meetings(
    body_id: int,
    key_phrase: str | None = None,
    status: str | None = None,
    start_date: str | None = None,
    end_date: str | None = None,
) -> list:
    """Find meetings of a governing body.

    Args:
        body_id: Governing body id.
        key_phrase: Only meetings whose transcript has this key phrase. When set, the other filters are ignored.
        status: One of "scheduled", "recorded", "transcribing", "archived".
        start_date: Earliest meeting date, YYYY-MM-DD.
        end_date: Latest meeting date, YYYY-MM-DD.
    """
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


@tool
def delete_meeting(meeting_id: int, confirmed: bool = False) -> dict:
    """Delete a meeting and its transcript and analysis.

    The app asks the user to confirm before this runs and sets confirmed itself; leave confirmed unset.
    """
    if not confirmed:
        return {"error": "confirmation_required", "detail": "Ask the user to confirm first."}
    return {"deleted": _run(meetings.delete_meeting, meeting_id=meeting_id)}


# --- audio and transcription ---

@tool
def request_audio_upload(filename: str) -> dict:
    """Get a one-hour URL the user can upload a meeting recording (MP3 or WAV, max 500 MB) to with an HTTP PUT.

    After the user says the upload is done, call attach_uploaded_audio with the returned audio_key.
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
    """Get a meeting's full transcript text. Very long transcripts are truncated."""
    text = _run(transcripts.get_transcript, meeting_id)
    if text is None:
        return {"error": "not_found", "detail": "No transcript for this meeting."}
    if isinstance(text, str) and len(text) > MAX_TRANSCRIPT_CHARS:
        return {"truncated": True, "transcript": text[:MAX_TRANSCRIPT_CHARS]}
    return text if isinstance(text, dict) else {"transcript": text}


@tool
def get_trending_entities(body_id: int) -> dict:
    """Get the 50 people, places and organizations mentioned most across a governing body's meetings."""
    return _run(analysis.get_trending_entities, body_id)


def meeting_has_transcript(meeting_id: int) -> bool:
    """True if the meeting exists and already has a transcript (not a tool; used for confirmation routing)."""
    meeting = _run(meetings.list_meeting, meeting_id)
    return isinstance(meeting, dict) and bool(meeting.get("transcript_available"))


TOOLS = [
    list_governing_bodies, get_governing_body, create_governing_body, update_governing_body, delete_governing_body,
    search_meetings, get_meeting, create_meeting, update_meeting, delete_meeting,
    request_audio_upload, attach_uploaded_audio, start_transcription, check_transcription,
    get_transcript, get_trending_entities,
]
