"""The admin's recording page for one meeting: upload a recording, then start and check its transcription.

The browser uploads the file straight to S3 with a signed form, since a Lambda can't take a 500 MB request. Every
step runs the archive's own code (meeting_transcript.transcription), the same code the clerks' agent uses.
"""
import json
import logging
from django.core.exceptions import PermissionDenied
from django.http import Http404, JsonResponse
from django.shortcuts import render
from meeting_transcript.aws.aws import get_s3_client
from meeting_transcript.transcription import service as transcription
from meeting_transcript.transcription import store as transcripts
from meeting_transcript.transcription import uploads
from archive_site.archive import run
from .models import Meeting
# The same as the terminal chat: check this often, and stop once the job is in one of these states
CHECK_EVERY_SECONDS = 30
FINISHED = ["COMPLETED", "FAILED", "SUPERSEDED"]
log = logging.getLogger(__name__)


def _meeting(model_admin, request, meeting_id) -> Meeting:
    """The meeting, if it exists and this clerk may change meetings."""
    meeting = Meeting.objects.select_related("body").filter(pk=meeting_id).first()
    if meeting is None:
        raise Http404("No such meeting")
    if not model_admin.has_change_permission(request, meeting):
        raise PermissionDenied
    return meeting


def _sent(request) -> dict:
    """The JSON object a button posted, or {} if it isn't one."""
    try:
        sent = json.loads(request.body or b"{}")
    except ValueError:
        return {}
    return sent if isinstance(sent, dict) else {}


def _answer(result) -> JsonResponse:
    """The archive's result as JSON; its error results are client errors."""
    failed = isinstance(result, dict) and "error" in result
    return JsonResponse(result, status=400 if failed else 200)


def _server_error(step: str) -> JsonResponse:
    log.exception("The recording page's %s step failed", step)
    return JsonResponse({"error": "server_error", "detail": f"The {step} step failed; try again in a moment."},
                        status=500)


def replace_warning(meeting: Meeting) -> str:
    """The same warning the clerks' agent gives before replacing a transcribed recording."""
    return (f"This replaces the recording for meeting \"{meeting.title}\" on {meeting.meeting_date} "
            f"({meeting.body.name}, id {meeting.pk}), clearing its transcript, entities and key phrases.")


def page(model_admin, request, meeting_id):
    meeting = _meeting(model_admin, request, meeting_id)
    context = {
        **model_admin.admin_site.each_context(request),
        "opts": Meeting._meta,
        "meeting": meeting,
        "title": f"Recording and transcription: {meeting.title}",
        "check_every_seconds": CHECK_EVERY_SECONDS,
        "finished": FINISHED,
        "max_bytes": transcription.MAX_AUDIO_BYTES
    }
    return render(request, "records/recording.html", context)


def upload_form(model_admin, request, meeting_id):
    """A signed S3 upload form for the file the clerk picked."""
    if request.method != "POST":
        return JsonResponse({"error": "method_not_allowed"}, status=405)
    _meeting(model_admin, request, meeting_id)
    filename = _sent(request).get("filename")
    if not isinstance(filename, str) or not filename:
        return JsonResponse({"error": "no_file", "detail": "Pick an MP3 or WAV file first."}, status=400)
    try:
        return _answer(uploads.request_upload(get_s3_client(), filename))
    except Exception:
        return _server_error("upload form")


def attach(model_admin, request, meeting_id):
    """Attach the uploaded file to the meeting. Replacing a transcribed recording needs {"confirm": true} first."""
    if request.method != "POST":
        return JsonResponse({"error": "method_not_allowed"}, status=405)
    meeting = _meeting(model_admin, request, meeting_id)
    sent = _sent(request)
    audio_key = sent.get("audio_key")
    if not isinstance(audio_key, str):
        return JsonResponse({"error": "invalid_audio_key", "detail": "Upload a file first."}, status=400)
    if meeting.transcript is not None and sent.get("confirm") is not True:
        return JsonResponse({"confirm": replace_warning(meeting)})

    def store_key(stored_key):
        return run(transcripts.attach_audio, stored_key, meeting_id=meeting.pk, body_id=meeting.body_id)

    try:
        return _answer(uploads.attach_upload(get_s3_client(), audio_key, store_key))
    except Exception:
        return _server_error("attach")


def start_transcription(model_admin, request, meeting_id):
    if request.method != "POST":
        return JsonResponse({"error": "method_not_allowed"}, status=405)
    meeting = _meeting(model_admin, request, meeting_id)
    try:
        return _answer(run(transcription.start_transcription_job, meeting.pk))
    except Exception:
        return _server_error("start transcription")


def check_transcription(model_admin, request, meeting_id):
    """The job's status. Once it's done this also stores the transcript and runs the entity and key-phrase analysis,
    which can take a while; the page just checks again."""
    if request.method != "POST":
        return JsonResponse({"error": "method_not_allowed"}, status=405)
    meeting = _meeting(model_admin, request, meeting_id)
    try:
        result = run(transcription.get_transcription_job, meeting.pk)
    except Exception:
        return _server_error("check transcription")
    if isinstance(result, dict):
        # The page links to the meeting for the transcript itself
        result.pop("transcript", None)
    return _answer(result)
