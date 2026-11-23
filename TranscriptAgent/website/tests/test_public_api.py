"""The public API: anyone can read the archive, nobody can change it, and it never calls an agent."""
from django.test import Client
from tests.helpers import give_transcript, new_body, new_meeting


def test_bodies_and_one_body(client):
    body = new_body()
    new_meeting(body.id)
    listed = client.get("/api/v1/bodies").json()
    assert listed == [{"id": body.id, "name": "Springfield City Council", "body": "council",
                       "description": "City council", "meeting_count": 1}]
    assert client.get(f"/api/v1/bodies/{body.id}").json()["name"] == "Springfield City Council"
    assert client.get("/api/v1/bodies/999999").status_code == 404
    # an id too big for Postgres is just not found, not a database error
    assert client.get("/api/v1/bodies/99999999999").status_code == 404


def test_meetings_filters_and_phrases(client):
    body = new_body()
    budget = new_meeting(body.id, "Budget Hearing", "2026-03-04")
    new_meeting(body.id, "Regular Session", "2026-04-01")
    give_transcript(budget.id, "We discussed parking downtown.", phrases=["parking downtown"])
    url = f"/api/v1/bodies/{body.id}/meetings"
    assert [m["title"] for m in client.get(url).json()] == ["Budget Hearing", "Regular Session"]
    assert [m["title"] for m in client.get(url, {"start_date": "2026-03-15"}).json()] == ["Regular Session"]
    assert [m["title"] for m in client.get(url, {"status": "archived"}).json()] == ["Budget Hearing"]
    assert [m["title"] for m in client.get(url, {"phrase": "parking"}).json()] == ["Budget Hearing"]
    assert client.get(url, {"status": "cancelled"}).status_code == 400
    assert client.get(url, {"start_date": "March"}).status_code == 400
    assert client.get("/api/v1/bodies/999999/meetings").status_code == 404


def test_a_meeting_and_its_transcript(client):
    body = new_body()
    meeting = new_meeting(body.id)
    assert client.get(f"/api/v1/meetings/{meeting.id}").json()["transcript_available"] is False
    assert client.get(f"/api/v1/meetings/{meeting.id}/transcript").json()["error"] == "no_transcript"
    give_transcript(meeting.id, "Call to order.", entities={"Main Street": 3})
    assert client.get(f"/api/v1/meetings/{meeting.id}/transcript").json()["transcript"] == "Call to order."
    assert client.get(f"/api/v1/meetings/{meeting.id}").json()["entities"] == ["Main Street"]
    assert client.get(f"/api/v1/bodies/{body.id}/trending-entities").json() == {"trending-entities": ["Main Street"]}
    assert client.get("/api/v1/meetings/999999").status_code == 404
    assert client.get("/api/v1/meetings/999999/transcript").json()["error"] == "not_found"


def test_searching_transcripts(client):
    body = new_body()
    meeting = new_meeting(body.id)
    give_transcript(meeting.id, "Councilmember Lee asked about Main Street parking again.")
    found = client.get("/api/v1/search", {"q": "main street"}).json()
    assert [m["id"] for m in found] == [meeting.id] and "Main Street" in found[0]["snippets"][0]
    assert client.get("/api/v1/search", {"q": "main street", "body_id": body.id + 1}).json() == []
    assert client.get("/api/v1/search").status_code == 400
    assert client.get("/api/v1/search", {"q": "x", "body_id": "one"}).status_code == 400


def test_the_api_only_reads(client):
    body = new_body()
    # a client that checks CSRF like a real browser's requests, so other methods get the 405 and not a CSRF page
    strict_client = Client(enforce_csrf_checks=True)
    for method in (strict_client.post, strict_client.put, strict_client.patch, strict_client.delete):
        response = method(f"/api/v1/bodies/{body.id}")
        assert response.status_code == 405 and response["Allow"] == "GET"
        assert response.json()["error"] == "method_not_allowed"
    assert client.get(f"/api/v1/bodies/{body.id}").status_code == 200


def test_the_api_index_lists_the_endpoints(client):
    assert "governing_bodies" in client.get("/api/v1/").json()
