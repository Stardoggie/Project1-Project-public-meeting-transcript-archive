import pytest


def test_create_meeting(client,governing_body):
    response = client.post(
        "/api/v1/governing-bodies/1/meetings",
        json={
            "title": "City Council Meeting",
            "meeting_date": "2026-09-03",
        },
    )
    assert response.status_code == 201

@pytest.mark.parametrize(
    "payload",
    [
        {},
        {"title": ""},
        {"title": None},
        {"meeting_date": "not-a-date"},
    ],
)

def test_create_meeting_invalid_input(client, payload):
    response = client.post(
        "/api/v1/governing-bodies/1/meetings",
        json=payload,
    )
    assert response.status_code == 400

def test_meetings_filter_by_key_phrase(client):
    response = client.get(
        "/api/v1/governing-bodies/1/meetings?key-phrase=budget"
    )
    assert response.status_code == 200

def test_delete_meeting_not_found(client):
    response = client.delete(
        "/api/v1/governing-bodies/1/meetings/999999",
        json={
            "id": 1,
        },
    )
    assert response.status_code == 404




