"""The clerks' admin: records are saved and deleted through the archive's store, and the recording page uploads,
attaches and transcribes with the archive's own code."""
import json
from botocore.stub import ANY
from records import recording
from tests.helpers import give_transcript, meeting_row, new_body, new_meeting
UPLOAD_KEY = "uploads/meeting-transcription-0123456789abcdef0123456789abcdef.mp3"
# The hidden fields a browser sends for the meeting page's read-only entity and key phrase tables
INLINE_FORMS = {"entities-TOTAL_FORMS": "0", "entities-INITIAL_FORMS": "0", "key_phrases-TOTAL_FORMS": "0",
                "key_phrases-INITIAL_FORMS": "0"}


def test_clerks_create_edit_and_delete_governing_bodies(client, clerk):
    made = client.post("/admin/records/governingbody/add/", {"name": "Parks Board", "body": "board",
                                                             "description": "Parks and recreation"})
    assert made.status_code == 302
    body_id = client.get("/api/v1/bodies").json()[0]["id"]
    client.post(f"/admin/records/governingbody/{body_id}/change/", {"name": "Parks and Trails Board", "body": "board",
                                                                    "description": "Parks and recreation"})
    assert client.get(f"/api/v1/bodies/{body_id}").json()["name"] == "Parks and Trails Board"
    # the archive's own rules: a description of at least 5 characters
    short = client.post("/admin/records/governingbody/add/", {"name": "X", "body": "board", "description": "abc"})
    assert short.status_code == 200 and len(client.get("/api/v1/bodies").json()) == 1
    client.post(f"/admin/records/governingbody/{body_id}/delete/", {"post": "yes"})
    assert client.get("/api/v1/bodies").json() == []


def test_clerks_create_edit_and_delete_meetings(client, clerk):
    body = new_body()
    made = client.post("/admin/records/meeting/add/", {"body": body.id, "title": "Budget Hearing",
                                                       "meeting_date": "2026-03-04"})
    assert made.status_code == 302
    meeting_id = client.get(f"/api/v1/bodies/{body.id}/meetings").json()[0]["id"]
    edit = client.post(f"/admin/records/meeting/{meeting_id}/change/", {"title": "Budget Hearing (continued)",
                                                                        "meeting_date": "2026-03-05", **INLINE_FORMS})
    assert edit.status_code == 302
    edited = client.get(f"/api/v1/meetings/{meeting_id}").json()
    assert edited["title"] == "Budget Hearing (continued)" and edited["meeting_date"] == "2026-03-05"
    client.post(f"/admin/records/meeting/{meeting_id}/delete/", {"post": "yes"})
    assert meeting_row(meeting_id) is None


def test_deleting_a_body_deletes_its_meetings_and_their_analysis(client, clerk):
    body = new_body()
    meeting = new_meeting(body.id)
    give_transcript(meeting.id, "Call to order.", phrases=["budget"], entities={"Main Street": 2})
    client.post(f"/admin/records/governingbody/{body.id}/delete/", {"post": "yes"})
    assert meeting_row(meeting.id) is None


def test_viewers_cant_use_the_recording_page(client, viewer):
    meeting = new_meeting(new_body().id)
    assert client.post(f"/admin/records/meeting/{meeting.id}/transcription/start/").status_code == 302


def test_the_upload_form_is_signed_for_the_picked_file(client, clerk, s3):
    meeting = new_meeting(new_body().id)
    url = f"/admin/records/meeting/{meeting.id}/recording/form/"
    form = client.post(url, json.dumps({"filename": "hearing.mp3"}), content_type="application/json").json()
    assert form["audio_key"].startswith("uploads/") and form["upload_fields"]["Content-Type"] == "audio/mpeg"
    refused = client.post(url, json.dumps({"filename": "notes.txt"}), content_type="application/json")
    assert refused.status_code == 400 and refused.json()["error"] == "unsupported_media_type"


def test_attaching_an_upload_marks_the_meeting_recorded(client, clerk, s3):
    meeting = new_meeting(new_body().id)
    s3.add_response("head_object", {"ContentLength": 1000}, {"Bucket": ANY, "Key": UPLOAD_KEY})
    s3.add_response("copy_object", {}, {"Bucket": ANY, "Key": ANY, "CopySource": {"Bucket": ANY, "Key": UPLOAD_KEY}})
    s3.add_response("delete_object", {}, {"Bucket": ANY, "Key": UPLOAD_KEY})
    attached = client.post(f"/admin/records/meeting/{meeting.id}/recording/attach/",
                           json.dumps({"audio_key": UPLOAD_KEY}), content_type="application/json")
    assert attached.status_code == 200
    row = meeting_row(meeting.id)
    assert row.status == "recorded" and row.audio_object_key.startswith("audio/")


def test_replacing_a_transcribed_recording_asks_first(client, clerk, s3):
    meeting = new_meeting(new_body().id)
    give_transcript(meeting.id, "Call to order.")
    url = f"/admin/records/meeting/{meeting.id}/recording/attach/"
    asked = client.post(url, json.dumps({"audio_key": UPLOAD_KEY}), content_type="application/json").json()
    assert "clearing its transcript" in asked["confirm"]
    assert meeting_row(meeting.id).transcript == "Call to order."
    s3.add_response("head_object", {"ContentLength": 1000}, {"Bucket": ANY, "Key": UPLOAD_KEY})
    s3.add_response("copy_object", {}, {"Bucket": ANY, "Key": ANY, "CopySource": {"Bucket": ANY, "Key": UPLOAD_KEY}})
    s3.add_response("delete_object", {}, {"Bucket": ANY, "Key": UPLOAD_KEY})
    client.post(url, json.dumps({"audio_key": UPLOAD_KEY, "confirm": True}), content_type="application/json")
    assert meeting_row(meeting.id).transcript is None


def test_starting_and_checking_a_transcription(client, clerk, monkeypatch):
    meeting = new_meeting(new_body().id)
    monkeypatch.setattr(recording.transcription, "start_transcription_job",
                        lambda meeting_id: {"job_name": "job-1", "status": "IN_PROGRESS"})
    monkeypatch.setattr(recording.transcription, "get_transcription_job",
                        lambda meeting_id: {"job_name": "job-1", "status": "COMPLETED", "transcript": "Call to order."})
    started = client.post(f"/admin/records/meeting/{meeting.id}/transcription/start/").json()
    assert started == {"job_name": "job-1", "status": "IN_PROGRESS"}
    checked = client.post(f"/admin/records/meeting/{meeting.id}/transcription/check/").json()
    # the page doesn't need the transcript itself; it's on the meeting's page
    assert checked == {"job_name": "job-1", "status": "COMPLETED"}


def test_the_recording_page_opens(client, clerk):
    meeting = new_meeting(new_body().id)
    page = client.get(f"/admin/records/meeting/{meeting.id}/recording/")
    assert page.status_code == 200 and b"Start transcription" in page.content
    assert client.get(f"/admin/records/meeting/{meeting.id}/change/").status_code == 200
