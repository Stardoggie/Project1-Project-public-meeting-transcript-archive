from unittest.mock import patch


def test_transcribe_meeting(client, meeting):
    with patch(
        "meeting_transcript.transcription.service.get_audio_key",
        return_value="audio/test-meeting.mp3",
    ), patch(
        "meeting_transcript.transcription.service.store_job_key"
    ), patch(
        "meeting_transcript.transcription.service.get_transcribe_client"
    ) as mock_client:
        response = client.post(
            f"/api/v1/governing-bodies/1/meetings/{meeting['id']}/transcribe"
        )
    assert response.status_code == 202
    assert response.status_code == 202


def test_transcribe_meeting_not_found(client):
    response = client.post(
        "/api/v1/governing-bodies/1/meetings/999999/transcribe"
    )
    assert response.status_code == 404


def test_get_transcription_status(client, meeting, monkeypatch):
    monkeypatch.setattr(
        "meeting_transcript.transcription.service.get_job_key",
        lambda meeting_id: "test-meeting"
    )
    class FakeTranscribeClient:
        def get_transcription_job(self, TranscriptionJobName):
            return {
                "TranscriptionJob": {
                    "TranscriptionJobName": TranscriptionJobName,
                    "TranscriptionJobStatus": "IN_PROGRESS",
                }
            }
    monkeypatch.setattr(
        "meeting_transcript.transcription.service.get_transcribe_client",
        lambda: FakeTranscribeClient()
    )
    response = client.post(
        f"/api/v1/governing-bodies/1/meetings/{meeting['id']}/transcribe/status"
    )
    assert response.status_code in (200, 202)



def test_get_transcription_status_not_found(client):
    response = client.post(
        "/api/v1/governing-bodies/1/meetings/999999/transcribe"
    )
    assert response.status_code == 404
