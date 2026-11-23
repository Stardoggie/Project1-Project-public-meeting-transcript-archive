import logging
import re
import uuid
from meeting_transcript.config import BUCKET_NAME
from meeting_transcript.transcription.service import ALLOWED_AUDIO_EXTENSIONS,MAX_AUDIO_BYTES
UPLOAD_URL_SECONDS = 3600
#uploads land in uploads/ first; an s3 lifecycle rule deletes anything left there after a day, so files that are
#never attached (cancelled, rejected, abandoned) don't pile up. attached recordings are moved into audio/
UPLOAD_PREFIX = "uploads/"
AUDIO_PREFIX = "audio/"
#the only key shape request_upload hands out
AUDIO_KEY_PATTERN = re.compile(r"uploads/meeting-transcription-[0-9a-f]{32}\.(mp3|wav)")
#the content type s3 requires for each format; the upload form is signed with it, so nothing else is accepted
AUDIO_CONTENT_TYPES = {"mp3": "audio/mpeg", "wav": "audio/wav"}
log = logging.getLogger(__name__)


def request_upload(s3,filename:str)->dict:
    """
        a signed one-hour upload form for a meeting recording (mp3 or wav, max 500 MB)
        a form rather than a plain link: s3 itself refuses a file over the size limit, an empty one,
        or one that isn't this audio type, before storing anything
        used by the clerks' agent and the website's admin
    """
    extension = filename.rsplit(".",1)[-1].lower()
    if extension not in ALLOWED_AUDIO_EXTENSIONS:
        return {"error": "unsupported_media_type", "detail": "Only MP3 and WAV are accepted."}
    audio_key = f"{UPLOAD_PREFIX}meeting-transcription-{uuid.uuid4().hex}.{extension}"
    content_type = AUDIO_CONTENT_TYPES[extension]
    upload = s3.generate_presigned_post(
        Bucket=BUCKET_NAME,
        Key=audio_key,
        Fields={"Content-Type": content_type},
        Conditions=[{"Content-Type": content_type}, ["content-length-range", 1, MAX_AUDIO_BYTES]],
        ExpiresIn=UPLOAD_URL_SECONDS
    )
    return {"upload_url": upload["url"], "upload_fields": upload["fields"], "audio_key": audio_key, "method": "POST"}


def attach_upload(s3,audio_key:str,attach)->dict:
    """
        moves an uploaded recording into audio/ and attaches it to a meeting
        attach is called with the stored key and does the database part (transcription.store.attach_audio inside
        the caller's app context); it returns that function's result, or an error dict
        replaces any earlier recording and clears that meeting's transcript, entities and key phrases
    """
    if not AUDIO_KEY_PATTERN.fullmatch(audio_key):
        return {"error": "invalid_audio_key", "detail": "Use the audio_key returned by request_audio_upload."}
    try:
        size = s3.head_object(Bucket=BUCKET_NAME,Key=audio_key)["ContentLength"]
    except Exception:
        return {"error": "upload_not_found", "detail": "The file has not been uploaded yet."}
    if size == 0 or size > MAX_AUDIO_BYTES:
        delete_from_s3(s3,audio_key)#it can never be used, so don't keep it
        return {"error": "invalid_size", "detail": f"File is {size} bytes; must be 1 byte to 500 MB."}
    #keep the recording for good in audio/; anything still in uploads/ is deleted by the lifecycle rule
    stored_key = new_audio_key(audio_key)
    try:
        s3.copy_object(Bucket=BUCKET_NAME,Key=stored_key,CopySource={"Bucket": BUCKET_NAME, "Key": audio_key})
    except Exception:
        log.exception("Couldn't move %s into %s",audio_key,AUDIO_PREFIX)
        return {"error": "upload_not_stored", "detail": "The upload couldn't be moved into storage; try attaching it again."}
    result = attach(stored_key)
    attached = isinstance(result, dict) and "error" not in result and result != {"out": "out_of_bounds"}
    if not attached:
        #nothing was attached: drop the stored copy but keep the upload, so attaching again still works
        delete_from_s3(s3,stored_key)
        if result == {"out": "out_of_bounds"}:
            return {"error": "wrong_governing_body", "detail": "Meeting is not in that governing body."}
        if result is None:
            return {"error": "not_found", "detail": "meeting not found"}
        return result
    delete_from_s3(s3,audio_key)
    return result


def new_audio_key(upload_key:str)->str:
    """
        a new name in audio/ for each attach, so two attaches of one upload never share (or delete) the same copy
        it keeps the upload's .mp3 or .wav, since transcribe reads the format from it
    """
    extension = upload_key.rsplit(".",1)[-1]
    return f"{AUDIO_PREFIX}meeting-transcription-{uuid.uuid4().hex}.{extension}"


def delete_from_s3(s3,key:str)->None:
    """
        deletes one object; if that fails it's only logged, since a leftover upload is removed by the lifecycle rule
    """
    try:
        s3.delete_object(Bucket=BUCKET_NAME,Key=key)
    except Exception:
        log.warning("Couldn't delete %s from S3",key,exc_info=True)
