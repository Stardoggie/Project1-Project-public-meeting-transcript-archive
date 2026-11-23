"""The public, read-only REST API: no sign-in, no agent, only GET.

These are the old Flask routes' reads. Each one calls the archive's own store function through its read-only app,
which only sends SELECT and SHOW, so nothing here can change the archive.
"""
import functools
import logging
from django.http import JsonResponse
from django.views.decorators.csrf import csrf_exempt
from pydantic import ValidationError
from meeting_transcript.analysis import store as analysis
from meeting_transcript.governing_body import store as bodies
from meeting_transcript.meetings import store as meetings
from meeting_transcript.transcription import store as transcripts
from archive_site.archive import dump, run
# Ids are Postgres integers, so a bigger one can't name a row
MAX_ID = 2**31 - 1
MAX_SEARCH_RESULTS = 25
log = logging.getLogger(__name__)


def _error(status: int, error: str, detail: str) -> JsonResponse:
    return JsonResponse({"error": error, "detail": detail}, status=status)


def _read(fn, *args, **kwargs):
    """A store function's result through the archive's read-only app, ready for JSON."""
    return dump(run(fn, *args, read_only=True, **kwargs))


def read_only(view):
    """GET only, and any unexpected error is a short JSON 500 (the full one stays in the log).

    CSRF-exempt so other methods get this 405 instead of Django's CSRF page; safe, since nothing here changes data."""

    @csrf_exempt
    @functools.wraps(view)
    def wrapper(request, *args, **kwargs):
        if request.method != "GET":
            response = _error(405, "method_not_allowed", "This API only reads the archive; use GET.")
            response["Allow"] = "GET"
            return response
        try:
            return view(request, *args, **kwargs)
        except Exception:
            log.exception("The public API failed on %s", request.path)
            return _error(500, "server_error", "The archive couldn't answer that; try again in a moment.")

    return wrapper


def _body_or_none(body_id: int):
    if body_id > MAX_ID:
        return None
    return _read(bodies.list_body, body_id)


@read_only
def index(request):
    """What the API offers, so a visitor can find their way around."""
    return JsonResponse({
        "governing_bodies": "/api/v1/bodies",
        "one_body": "/api/v1/bodies/<id>",
        "a_body's_meetings": "/api/v1/bodies/<id>/meetings?status=&start_date=YYYY-MM-DD&end_date=YYYY-MM-DD&phrase=",
        "trending_entities": "/api/v1/bodies/<id>/trending-entities",
        "one_meeting": "/api/v1/meetings/<id>",
        "a_meeting's_transcript": "/api/v1/meetings/<id>/transcript",
        "search_transcripts": "/api/v1/search?q=<text>&body_id=<id>"
    })


@read_only
def body_list(request):
    return JsonResponse(_read(bodies.list_bodies), safe=False)


@read_only
def body_detail(request, body_id: int):
    body = _body_or_none(body_id)
    if body is None:
        return _error(404, "not_found", "governing body not found")
    return JsonResponse(body)


@read_only
def body_meetings(request, body_id: int):
    """A body's meetings, filtered by status and date range, or only those with a key phrase."""
    if _body_or_none(body_id) is None:
        return _error(404, "not_found", "governing body not found")
    phrase = request.GET.get("phrase", "").strip()
    if phrase:
        return JsonResponse(_read(analysis.get_meeting_topic, body_id, phrase), safe=False)
    filters = {name: request.GET.get(name) or None for name in ["status", "start_date", "end_date"]}
    try:
        found = _read(meetings.list_meetings, body_id, **filters)
    except (ValidationError, ValueError):
        return _error(400, "invalid_filter", "status is scheduled, recorded, transcribing or archived; "
                      "dates are YYYY-MM-DD.")
    return JsonResponse(found, safe=False)


@read_only
def trending_entities(request, body_id: int):
    if _body_or_none(body_id) is None:
        return _error(404, "not_found", "governing body not found")
    return JsonResponse(_read(analysis.get_trending_entities, body_id))


@read_only
def meeting_detail(request, meeting_id: int):
    meeting = _read(meetings.list_meeting, meeting_id) if meeting_id <= MAX_ID else None
    if meeting is None:
        return _error(404, "not_found", "meeting not found")
    return JsonResponse(meeting)


@read_only
def meeting_transcript(request, meeting_id: int):
    if meeting_id > MAX_ID:
        return _error(404, "not_found", "meeting not found")
    transcript = _read(transcripts.get_transcript, meeting_id)
    if isinstance(transcript, dict):
        return _error(404, "not_found", "meeting not found")
    if transcript is None:
        return _error(404, "no_transcript", "This meeting has no transcript yet.")
    return JsonResponse({"meeting_id": meeting_id, "transcript": transcript})


@read_only
def search(request):
    """Meetings whose transcript contains some text, with short excerpts around the matches."""
    text = request.GET.get("q", "").strip()
    if not text:
        return _error(400, "missing_query", "Add ?q=<text to look for>.")
    body_id = request.GET.get("body_id")
    if body_id is not None and not (body_id.isascii() and body_id.isdigit() and int(body_id) <= MAX_ID):
        return _error(400, "invalid_body_id", "body_id is a governing body's id (a whole number).")
    found = _read(meetings.search_transcripts, text, body_id=int(body_id) if body_id else None,
                  limit=MAX_SEARCH_RESULTS)
    return JsonResponse(found, safe=False)
