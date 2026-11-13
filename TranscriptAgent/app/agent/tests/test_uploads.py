"""Uploads: the signed S3 form limits size and type, and attaching moves a recording from uploads/ into audio/."""
import base64
import json
import boto3
import pytest
from botocore.stub import ANY, Stubber
import transcript_tools as t
from meeting_transcript.transcription import service as transcription
UPLOAD_KEY = "uploads/meeting-transcription-0123456789abcdef0123456789abcdef.mp3"
STORED_KEY = "audio/meeting-transcription-0123456789abcdef0123456789abcdef.mp3"


@pytest.fixture
def s3(monkeypatch):
    """A real S3 client with fake credentials whose API calls are checked by a Stubber instead of reaching AWS."""
    client = boto3.client("s3", region_name="us-east-1", aws_access_key_id="testing", aws_secret_access_key="testing")
    monkeypatch.setattr(t, "get_s3_client", lambda: client)
    with Stubber(client) as stubber:
        yield stubber
        stubber.assert_no_pending_responses()


def new_meeting():
    body = t.create_governing_body.invoke({"name": "Upload Test Council", "body": "council", "description": "Upload test body"})
    meeting = t.create_meeting.invoke({"body_id": body["id"], "title": "Upload Test Meeting", "meeting_date": "2026-11-13"})
    return body, meeting


def test_the_upload_form_limits_size_and_type_and_stages_in_uploads(s3):
    upload = t.request_audio_upload.invoke({"filename": "hearing.MP3"})
    assert upload["method"] == "POST" and upload["audio_key"].startswith("uploads/")
    assert upload["upload_fields"]["Content-Type"] == "audio/mpeg"
    # the signed policy is what S3 enforces, so check its conditions rather than just the fields
    policy = json.loads(base64.b64decode(upload["upload_fields"]["policy"]))
    assert ["content-length-range", 1, transcription.MAX_AUDIO_BYTES] in policy["conditions"]
    assert {"Content-Type": "audio/mpeg"} in policy["conditions"]
    assert {"key": upload["audio_key"]} in policy["conditions"]
    assert t.request_audio_upload.invoke({"filename": "notes.txt"})["error"] == "unsupported_media_type"


def test_attaching_moves_the_recording_into_audio_and_removes_the_upload(s3):
    body, meeting = new_meeting()
    s3.add_response("head_object", {"ContentLength": 1000}, {"Bucket": ANY, "Key": UPLOAD_KEY})
    s3.add_response("copy_object", {}, {"Bucket": ANY, "Key": STORED_KEY, "CopySource": {"Bucket": ANY, "Key": UPLOAD_KEY}})
    s3.add_response("delete_object", {}, {"Bucket": ANY, "Key": UPLOAD_KEY})
    result = t.attach_uploaded_audio.invoke({"body_id": body["id"], "meeting_id": meeting["id"], "audio_key": UPLOAD_KEY})
    # the meeting points at the copy in audio/, which the lifecycle rule never touches
    assert result == {"audio_key": STORED_KEY}
    assert t.get_meeting.invoke({"meeting_id": meeting["id"]})["status"] == "recorded"


def test_an_oversized_upload_is_deleted_and_not_attached(s3):
    body, meeting = new_meeting()
    s3.add_response("head_object", {"ContentLength": transcription.MAX_AUDIO_BYTES + 1}, {"Bucket": ANY, "Key": UPLOAD_KEY})
    s3.add_response("delete_object", {}, {"Bucket": ANY, "Key": UPLOAD_KEY})
    result = t.attach_uploaded_audio.invoke({"body_id": body["id"], "meeting_id": meeting["id"], "audio_key": UPLOAD_KEY})
    assert result["error"] == "invalid_size"
    assert t.get_meeting.invoke({"meeting_id": meeting["id"]})["status"] == "scheduled"


def test_a_failed_attach_drops_the_stored_copy_but_keeps_the_upload_for_a_retry(s3):
    body, meeting = new_meeting()
    s3.add_response("head_object", {"ContentLength": 1000}, {"Bucket": ANY, "Key": UPLOAD_KEY})
    s3.add_response("copy_object", {}, {"Bucket": ANY, "Key": STORED_KEY, "CopySource": {"Bucket": ANY, "Key": UPLOAD_KEY}})
    # only the copy in audio/ is deleted; no delete of the upload is expected, so the Stubber would fail on one
    s3.add_response("delete_object", {}, {"Bucket": ANY, "Key": STORED_KEY})
    result = t.attach_uploaded_audio.invoke({"body_id": body["id"], "meeting_id": meeting["id"] + 999, "audio_key": UPLOAD_KEY})
    assert result["error"] == "not_found"


def test_only_upload_keys_from_the_form_can_be_attached(s3):
    body, meeting = new_meeting()
    result = t.attach_uploaded_audio.invoke({"body_id": body["id"], "meeting_id": meeting["id"], "audio_key": STORED_KEY})
    assert result["error"] == "invalid_audio_key"
