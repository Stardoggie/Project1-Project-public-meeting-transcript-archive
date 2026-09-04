import pytest


BASE_URL = "/api/v1/governing-bodies"



def test_create_body(client):
    response = client.post(
        "/api/v1/governing-bodies",
        json={
            "name": "Test Governing Body",
            "body": "board",
            "description": "This is a test governing body.",
        },
    )
    assert response.status_code == 201

def test_get_bodies(client):
    response = client.get(BASE_URL)
    assert response.status_code == 200


def test_get_specific_body(client, governing_body):
    response = client.get(
        f"{BASE_URL}/{governing_body.id}"
    )
    assert response.status_code == 200


def test_get_specific_body_not_found(client):
    response = client.get(
        f"{BASE_URL}/999999"
    )
    assert response.status_code == 404




def test_create_body_invalid(client):
    response = client.post(
        BASE_URL,
        json={
            "name": "Test Governing Body",
            "body": "board",
            # missing desc
        },
    )
    assert response.status_code == 400


def test_create_body_extra_field(client):
    response = client.post(
        BASE_URL,
        json={
            "name": "Test Governing Body",
            "body": "board",
            "description": "test bad extra stuff.",
            "something_else": "not allowed",
        },
    )
    assert response.status_code == 400


def test_update_body(client, governing_body):
    response = client.put(
        f"{BASE_URL}/{governing_body.id}",
        json={
            "name": "Updated Governing Body",
            "body": "board",
            "description": "update the dec.",
        },
    )
    assert response.status_code == 200


def test_update_body_not_found(client):
    response = client.put(
        f"{BASE_URL}/999999",
        json={
            "name": "Updated Governing Body",
            "body": "board",
            "description": "update the desc.",
        },
    )
    assert response.status_code == 404


def test_delete_body(client, governing_body):
    response = client.delete(
        f"{BASE_URL}/{governing_body.id}",
        json={
            "id": governing_body.id,
        },
    )
    assert response.status_code == 204


def test_delete_body_wrong_id(client, governing_body):
    response = client.delete(
        f"{BASE_URL}/{governing_body.id}",
        json={
            "id": 999999,
        },
    )
    assert response.status_code == 400

