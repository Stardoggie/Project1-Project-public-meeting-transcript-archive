import io
import json
import threading
import time
import boto3
import pytest
from botocore.exceptions import ClientError
from botocore.stub import Stubber
from meeting_transcript.extensions import db
from meeting_transcript.meetings.models_db import Meeting
from meeting_transcript.transcription import service, store
AUDIO_KEY = "audio/meeting-transcription-0123456789abcdef0123456789abcdef.mp3"
JOB_NAME = "meeting-transcription-0123456789abcdef0123456789abcdef"
NOT_FOUND = {"error": "not_found", "detail": "Meeting not found."}


def test_transcribe_meeting_not_found(app):
    assert service.start_transcription_job(999999) == NOT_FOUND


def test_get_transcription_status(meeting, monkeypatch):
    monkeypatch.setattr(
        "meeting_transcript.transcription.service.get_job_key",
        lambda meeting_id: "test-meeting"
    )
    class FakeTranscribeClient:
        def get_transcription_job(self, TranscriptionJobName):
            return {
                "TranscriptionJob": {
                    "TranscriptionJobName": TranscriptionJobName,
                    "TranscriptionJobStatus": "IN_PROGRESS"
                }
            }
    monkeypatch.setattr(
        "meeting_transcript.transcription.service.get_transcribe_client",
        lambda: FakeTranscribeClient()
    )
    assert service.get_transcription_job(meeting["id"]) == {"job_name": "test-meeting", "status": "IN_PROGRESS"}


def test_get_transcription_status_not_found(app):
    assert service.get_transcription_job(999999) == NOT_FOUND


# --- start/check against a stubbed Transcribe client ---

@pytest.fixture
def transcribe(monkeypatch):
    # dummy credentials: the stubber never sends a request
    client = boto3.client(
        "transcribe", region_name="us-east-1", aws_access_key_id="testing", aws_secret_access_key="testing"
    )
    monkeypatch.setattr(service, "get_transcribe_client", lambda: client)
    with Stubber(client) as stubber:
        yield stubber
        stubber.assert_no_pending_responses()


@pytest.fixture
def s3(monkeypatch):
    client = boto3.client(
        "s3", region_name="us-east-1", aws_access_key_id="testing", aws_secret_access_key="testing"
    )
    monkeypatch.setattr(service, "get_s3_client", lambda: client)
    with Stubber(client) as stubber:
        yield stubber
        stubber.assert_no_pending_responses()


def _expect_head(stubber, exists=True):
    params = {"Bucket": service.BUCKET_NAME, "Key": f"transcripts/{JOB_NAME}.json"}
    if exists:
        stubber.add_response("head_object", {}, params)
    else:
        # head_object has no body, so a missing key comes back as a bare 404
        stubber.add_client_error("head_object", service_error_code="404", http_status_code=404, expected_params=params)


@pytest.fixture
def attached(meeting, governing_body):
    store.attach_audio(AUDIO_KEY, meeting_id=meeting["id"], body_id=governing_body.id)
    return meeting["id"]


def _job(status):
    return {"TranscriptionJob": {"TranscriptionJobName": JOB_NAME, "TranscriptionJobStatus": status}}


def _expect_get(stubber, status):
    stubber.add_response("get_transcription_job", _job(status), {"TranscriptionJobName": JOB_NAME})


def _expect_missing(stubber):
    stubber.add_client_error(
        "get_transcription_job",
        service_error_code="BadRequestException",
        service_message="The requested job couldn't be found. Check the job name and try your request again.",
        http_status_code=400,
        expected_params={"TranscriptionJobName": JOB_NAME}
    )


def _expect_start(stubber):
    stubber.add_response(
        "start_transcription_job",
        _job("IN_PROGRESS"),
        {
            "TranscriptionJobName": JOB_NAME,
            "LanguageCode": "en-US",
            "MediaFormat": "mp3",
            "Media": {"MediaFileUri": f"s3://{service.BUCKET_NAME}/{AUDIO_KEY}"},
            "OutputBucketName": service.BUCKET_NAME,
            "OutputKey": f"transcripts/{JOB_NAME}.json"
        }
    )


def _fake_transcript(monkeypatch, text):
    body = json.dumps({"results": {"transcripts": [{"transcript": text}]}}).encode()
    monkeypatch.setattr(service, "get_object", lambda bucket, key: {"Body": io.BytesIO(body)})
    analysed = []
    monkeypatch.setattr(service, "comprehend_entities_and_phrases", lambda text, meeting_id, **kwargs: analysed.append(text))
    return analysed


def _meeting(meeting_id):
    db.session.expire_all()
    return db.session.get(Meeting, meeting_id)


def test_start_fresh_job(attached, transcribe):
    _expect_missing(transcribe)
    _expect_start(transcribe)
    assert service.start_transcription_job(attached) == {"job_name": JOB_NAME, "status": "IN_PROGRESS"}
    record = _meeting(attached)
    assert (record.transcribe_job_name, record.status) == (JOB_NAME, "transcribing")


@pytest.mark.parametrize("status", ["QUEUED", "IN_PROGRESS"])
def test_start_existing_running_job_stores_job_key(attached, transcribe, status):
    _expect_get(transcribe, status)
    assert service.start_transcription_job(attached) == {"job_name": JOB_NAME, "status": status}
    record = _meeting(attached)
    assert (record.transcribe_job_name, record.status) == (JOB_NAME, "transcribing")


def test_start_existing_completed_job_stores_job_key(attached, transcribe, s3):
    _expect_get(transcribe, "COMPLETED")
    _expect_head(s3)
    result = service.start_transcription_job(attached)
    assert result["status"] == "COMPLETED"
    record = _meeting(attached)
    assert (record.transcribe_job_name, record.status) == (JOB_NAME, "transcribing")


def test_start_existing_failed_job_is_deleted_and_restarted(attached, transcribe):
    _expect_get(transcribe, "FAILED")
    transcribe.add_response("delete_transcription_job", {}, {"TranscriptionJobName": JOB_NAME})
    _expect_start(transcribe)
    assert service.start_transcription_job(attached) == {"job_name": JOB_NAME, "status": "IN_PROGRESS"}
    record = _meeting(attached)
    assert (record.transcribe_job_name, record.status) == (JOB_NAME, "transcribing")


def test_reattach_same_key_while_running_then_check_stores_transcript(
    attached, governing_body, transcribe, monkeypatch
):
    analysed = _fake_transcript(monkeypatch, "The council approved the budget.")
    _expect_missing(transcribe)
    _expect_start(transcribe)
    service.start_transcription_job(attached)
    # re-attaching clears the job name while the job keeps running in Transcribe
    store.attach_audio(AUDIO_KEY, meeting_id=attached, body_id=governing_body.id)
    assert _meeting(attached).transcribe_job_name is None
    _expect_get(transcribe, "IN_PROGRESS")
    service.start_transcription_job(attached)
    _expect_get(transcribe, "COMPLETED")
    result = service.get_transcription_job(attached)
    assert result == {"job_name": JOB_NAME, "status": "COMPLETED", "transcript": "The council approved the budget."}
    record = _meeting(attached)
    assert (record.transcript, record.status) == ("The council approved the budget.", "archived")
    assert analysed == ["The council approved the budget."]


def test_reattach_same_key_after_completed_then_check_stores_transcript(
    attached, governing_body, transcribe, s3, monkeypatch
):
    _fake_transcript(monkeypatch, "Second pass.")
    store.attach_audio(AUDIO_KEY, meeting_id=attached, body_id=governing_body.id)
    _expect_get(transcribe, "COMPLETED")
    _expect_head(s3)
    service.start_transcription_job(attached)
    _expect_get(transcribe, "COMPLETED")
    assert service.get_transcription_job(attached)["transcript"] == "Second pass."
    assert _meeting(attached).status == "archived"


def test_check_silent_audio_returns_stored_text(attached, transcribe, monkeypatch):
    analysed = _fake_transcript(monkeypatch, "")
    store.store_job_key(attached, JOB_NAME)
    _expect_get(transcribe, "COMPLETED")
    result = service.get_transcription_job(attached)
    assert result["transcript"] == "No audio was detected."
    assert _meeting(attached).transcript == "No audio was detected."
    assert analysed == [""]


def test_missing_meeting_is_not_found(app):
    not_found = {"error": "not_found", "detail": "Meeting not found."}
    assert service.start_transcription_job(999999) == not_found
    assert service.get_transcription_job(999999) == not_found
    assert store.get_transcript(999999) == not_found


def test_existing_meeting_without_transcript(meeting):
    assert store.get_transcript(meeting["id"]) is None


def _archive(meeting_id, text="Archived text."):
    store.store_job_key(meeting_id, JOB_NAME)
    store.store_transcription(meeting_id, text, JOB_NAME)


def test_start_on_archived_meeting_changes_nothing(attached, transcribe, s3):
    _archive(attached)
    _expect_get(transcribe, "COMPLETED")
    # no s3 or transcribe calls beyond the status lookup
    assert service.start_transcription_job(attached) == {
        "job_name": JOB_NAME, "status": "COMPLETED", "detail": "Already transcribed."
    }
    record = _meeting(attached)
    assert (record.transcribe_job_name, record.transcript, record.status) == (JOB_NAME, "Archived text.", "archived")


def test_start_completed_job_with_missing_output_restarts(attached, transcribe, s3):
    _expect_get(transcribe, "COMPLETED")
    _expect_head(s3, exists=False)
    transcribe.add_response("delete_transcription_job", {}, {"TranscriptionJobName": JOB_NAME})
    _expect_start(transcribe)
    assert service.start_transcription_job(attached) == {"job_name": JOB_NAME, "status": "IN_PROGRESS"}
    record = _meeting(attached)
    assert (record.transcribe_job_name, record.status) == (JOB_NAME, "transcribing")


def test_check_completed_job_with_missing_output_reverts_then_restarts(attached, transcribe, s3, monkeypatch):
    def missing(bucket, key):
        raise ClientError({"Error": {"Code": "NoSuchKey", "Message": "missing"}}, "GetObject")
    monkeypatch.setattr(service, "get_object", missing)
    store.store_job_key(attached, JOB_NAME)
    _expect_get(transcribe, "COMPLETED")
    result = service.get_transcription_job(attached)
    assert result["status"] == "FAILED"
    assert "missing" in result["failure_reason"]
    record = _meeting(attached)
    assert (record.status, record.transcript) == ("recorded", None)
    # starting again replaces the job instead of reporting COMPLETED forever
    _expect_get(transcribe, "COMPLETED")
    _expect_head(s3, exists=False)
    transcribe.add_response("delete_transcription_job", {}, {"TranscriptionJobName": JOB_NAME})
    _expect_start(transcribe)
    assert service.start_transcription_job(attached)["status"] == "IN_PROGRESS"


def test_check_other_s3_error_is_raised(attached, transcribe, monkeypatch):
    def denied(bucket, key):
        raise ClientError({"Error": {"Code": "AccessDenied", "Message": "denied"}}, "GetObject")
    monkeypatch.setattr(service, "get_object", denied)
    store.store_job_key(attached, JOB_NAME)
    _expect_get(transcribe, "COMPLETED")
    with pytest.raises(ClientError):
        service.get_transcription_job(attached)
    assert _meeting(attached).status == "transcribing"


def _delete_meeting(meeting_id):
    db.session.delete(db.session.get(Meeting, meeting_id))
    db.session.commit()


def test_check_meeting_deleted_during_download_is_not_found(attached, transcribe, monkeypatch):
    body = json.dumps({"results": {"transcripts": [{"transcript": "Gone."}]}}).encode()

    def fetch_then_delete(bucket, key):
        _delete_meeting(attached)
        return {"Body": io.BytesIO(body)}
    monkeypatch.setattr(service, "get_object", fetch_then_delete)
    monkeypatch.setattr(service, "comprehend_entities_and_phrases", lambda text, meeting_id, **kwargs: pytest.fail("analysed"))
    store.store_job_key(attached, JOB_NAME)
    _expect_get(transcribe, "COMPLETED")
    assert service.get_transcription_job(attached) == {"error": "not_found", "detail": "Meeting not found."}


def test_check_meeting_deleted_before_analysis_is_not_found(attached, transcribe, monkeypatch):
    _fake_transcript(monkeypatch, "Gone later.")
    real_store = service.store_transcription

    def store_then_delete(meeting_id, text, job_key):
        stored = real_store(meeting_id, text, job_key)
        _delete_meeting(meeting_id)
        return stored
    monkeypatch.setattr(service, "store_transcription", store_then_delete)
    monkeypatch.setattr(service, "comprehend_entities_and_phrases", lambda text, meeting_id, **kwargs: pytest.fail("analysed"))
    store.store_job_key(attached, JOB_NAME)
    _expect_get(transcribe, "COMPLETED")
    assert service.get_transcription_job(attached) == {"error": "not_found", "detail": "Meeting not found."}


def test_store_helpers_handle_missing_meeting(app):
    assert store.store_transcription(999999, "text", JOB_NAME) is None
    assert store.claim_analysis(999999, JOB_NAME) is None
    assert store.already_transcribed(999999, JOB_NAME) is False
    store.store_job_key(999999, JOB_NAME)
    store.revert_transcribe_failure(999999)


def test_concurrent_start_conflict_reports_existing_job(attached, transcribe):
    _expect_missing(transcribe)
    transcribe.add_client_error(
        "start_transcription_job",
        service_error_code="ConflictException",
        service_message="The requested job name already exists. Use a different job name.",
        http_status_code=409
    )
    _expect_get(transcribe, "IN_PROGRESS")
    assert service.start_transcription_job(attached) == {"job_name": JOB_NAME, "status": "IN_PROGRESS"}
    record = _meeting(attached)
    assert (record.transcribe_job_name, record.status) == (JOB_NAME, "transcribing")


OTHER_AUDIO_KEY = "audio/meeting-transcription-fedcba9876543210fedcba9876543210.mp3"


def test_check_discards_a_transcript_for_audio_replaced_during_download(attached, governing_body, transcribe, monkeypatch):
    body = json.dumps({"results": {"transcripts": [{"transcript": "Old recording."}]}}).encode()

    def fetch_then_replace(bucket, key):
        store.attach_audio(OTHER_AUDIO_KEY, meeting_id=attached, body_id=governing_body.id)
        return {"Body": io.BytesIO(body)}
    monkeypatch.setattr(service, "get_object", fetch_then_replace)
    monkeypatch.setattr(service, "comprehend_entities_and_phrases", lambda text, meeting_id, **kwargs: pytest.fail("analysed"))
    store.store_job_key(attached, JOB_NAME)
    _expect_get(transcribe, "COMPLETED")
    assert service.get_transcription_job(attached)["status"] == "SUPERSEDED"
    record = _meeting(attached)
    assert (record.transcript, record.status, record.audio_object_key) == (None, "recorded", OTHER_AUDIO_KEY)


class _CompletedTranscribe:
    def get_transcription_job(self, TranscriptionJobName):
        return _job("COMPLETED")


def test_concurrent_checks_analyse_once(app, attached, monkeypatch):
    from meeting_transcript.analysis.store import add_entities_and_phrases
    from meeting_transcript.meetings.models_db import Entities
    body = json.dumps({"results": {"transcripts": [{"transcript": "The council approved the budget."}]}}).encode()
    monkeypatch.setattr(service, "get_transcribe_client", lambda: _CompletedTranscribe())
    monkeypatch.setattr(service, "get_object", lambda bucket, key: {"Body": io.BytesIO(body)})
    store.store_job_key(attached, JOB_NAME)

    def check_in_another_session():
        with app.app_context():
            results.append(service.get_transcription_job(attached))
    other = threading.Thread(target=check_in_another_session)
    results, analysed = [], []

    def analyse(text, meeting_id, job_key=None):
        analysed.append(meeting_id)
        if len(analysed) == 1:
            # the second check starts while this one is still analysing
            other.start()
            time.sleep(0.3)
        add_entities_and_phrases({"budget vote"}, {"springfield": 1}, meeting_id, job_key)
    monkeypatch.setattr(service, "comprehend_entities_and_phrases", analyse)
    assert service.get_transcription_job(attached)["status"] == "COMPLETED"
    other.join(timeout=10)
    assert not other.is_alive()
    # the second check came while the first was analysing, so it doesn't report completed yet
    assert results[0]["status"] == "ANALYZING"
    assert analysed == [attached]
    db.session.expire_all()
    assert db.session.query(Entities).filter_by(meeting_id=attached).count() == 1


def _analysis_that_runs(app, other_session_work, monkeypatch):
    """A comprehend fake that does other_session_work in another session mid-analysis, then stores its results."""
    from meeting_transcript.analysis.store import add_entities_and_phrases
    body = json.dumps({"results": {"transcripts": [{"transcript": "The council approved the budget."}]}}).encode()
    monkeypatch.setattr(service, "get_transcribe_client", lambda: _CompletedTranscribe())
    monkeypatch.setattr(service, "get_object", lambda bucket, key: {"Body": io.BytesIO(body)})
    stored = []

    def analyse(text, meeting_id, job_key=None):
        def work():
            with app.app_context():
                other_session_work(meeting_id)
        other = threading.Thread(target=work)
        other.start()
        other.join(timeout=5)
        assert not other.is_alive(), "blocked by the analysis"
        stored.append(add_entities_and_phrases({"budget vote"}, {"springfield": 1}, meeting_id, job_key))
    monkeypatch.setattr(service, "comprehend_entities_and_phrases", analyse)
    return stored


def test_deleting_a_meeting_during_analysis_succeeds_and_the_results_are_dropped(app, attached, monkeypatch):
    from meeting_transcript.meetings import store as meetings_store
    deleted = []
    stored = _analysis_that_runs(app, lambda meeting_id: deleted.append(meetings_store.delete_meeting(meeting_id)), monkeypatch)
    store.store_job_key(attached, JOB_NAME)
    service.get_transcription_job(attached)
    assert deleted == [True] and stored == [False] and _meeting(attached) is None


def test_new_audio_during_analysis_doesnt_wait_and_drops_the_old_results(app, attached, governing_body, monkeypatch):
    from meeting_transcript.meetings.models_db import Entities
    stored = _analysis_that_runs(
        app, lambda meeting_id: store.attach_audio(OTHER_AUDIO_KEY, meeting_id=meeting_id, body_id=governing_body.id), monkeypatch)
    store.store_job_key(attached, JOB_NAME)
    service.get_transcription_job(attached)
    record = _meeting(attached)
    assert stored == [False] and (record.transcript, record.analysis_completed) == (None, False)
    assert db.session.query(Entities).filter_by(meeting_id=attached).count() == 0


def test_a_failed_analysis_is_retried_by_the_next_check(attached, monkeypatch):
    body = json.dumps({"results": {"transcripts": [{"transcript": "Retry me."}]}}).encode()
    monkeypatch.setattr(service, "get_transcribe_client", lambda: _CompletedTranscribe())
    monkeypatch.setattr(service, "get_object", lambda bucket, key: {"Body": io.BytesIO(body)})
    store.store_job_key(attached, JOB_NAME)

    def broken(text, meeting_id, job_key=None):
        raise RuntimeError("comprehend is down")
    monkeypatch.setattr(service, "comprehend_entities_and_phrases", broken)
    with pytest.raises(RuntimeError):
        service.get_transcription_job(attached)
    assert _meeting(attached).analysis_completed is False
    analysed = _fake_transcript(monkeypatch, "Retry me.")
    service.get_transcription_job(attached)
    assert analysed == ["Retry me."]


def test_attaching_to_the_wrong_body_releases_the_row(app, attached, governing_body):
    assert store.attach_audio(OTHER_AUDIO_KEY, meeting_id=attached, body_id=governing_body.id + 1) == {"out": "out_of_bounds"}
    assert not db.session().in_transaction()


def test_an_analysis_abandoned_by_a_dead_process_is_claimed_again_after_the_timeout(attached, monkeypatch):
    from datetime import datetime, timedelta, timezone
    body = json.dumps({"results": {"transcripts": [{"transcript": "Crash test."}]}}).encode()
    monkeypatch.setattr(service, "get_transcribe_client", lambda: _CompletedTranscribe())
    monkeypatch.setattr(service, "get_object", lambda bucket, key: {"Body": io.BytesIO(body)})
    store.store_job_key(attached, JOB_NAME)

    def dies(text, meeting_id, job_key=None):
        raise SystemExit("the process is killed mid-analysis")  # nothing gets to release the claim
    monkeypatch.setattr(service, "comprehend_entities_and_phrases", dies)
    with pytest.raises(SystemExit):
        service.get_transcription_job(attached)
    db.session.rollback()
    record = _meeting(attached)
    assert record.analysis_completed is False and record.analysis_claimed_at is not None
    # a fresh claim is still running as far as anyone can tell
    analysed = _fake_transcript(monkeypatch, "Crash test.")
    assert service.get_transcription_job(attached)["status"] == "ANALYZING" and analysed == []
    # once it's older than the timeout, the next check takes it over
    record = _meeting(attached)
    record.analysis_claimed_at = datetime.now(timezone.utc) - store.ANALYSIS_CLAIM_TIMEOUT - timedelta(minutes=1)
    db.session.commit()
    assert service.get_transcription_job(attached)["status"] == "COMPLETED" and analysed == ["Crash test."]


def test_completed_is_reported_only_once_results_are_stored(attached, monkeypatch):
    from meeting_transcript.analysis.store import add_entities_and_phrases
    body = json.dumps({"results": {"transcripts": [{"transcript": "Stored."}]}}).encode()
    monkeypatch.setattr(service, "get_transcribe_client", lambda: _CompletedTranscribe())
    monkeypatch.setattr(service, "get_object", lambda bucket, key: {"Body": io.BytesIO(body)})
    monkeypatch.setattr(service, "comprehend_entities_and_phrases",
                        lambda text, meeting_id, job_key=None: add_entities_and_phrases(set(), {"springfield": 1}, meeting_id, job_key))
    store.store_job_key(attached, JOB_NAME)
    assert service.get_transcription_job(attached)["status"] == "COMPLETED"
    record = _meeting(attached)
    assert record.analysis_completed is True and record.analysis_claimed_at is None
    # and later checks report it completed without analysing again
    monkeypatch.setattr(service, "comprehend_entities_and_phrases", lambda text, meeting_id, **kwargs: pytest.fail("analysed"))
    assert service.get_transcription_job(attached)["status"] == "COMPLETED"
