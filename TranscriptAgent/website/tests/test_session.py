"""Signing in and out from the Vue frontend, through the JSON endpoints under /api/session."""
import json
from django.contrib.auth.models import User
from django.test import Client
from tests.conftest import PASSWORD


def _post(client, path, body=None, **kwargs):
    return client.post(path, json.dumps(body or {}), content_type="application/json", **kwargs)


def test_nobody_is_signed_in_at_first_and_the_csrf_cookie_is_set(client):
    response = client.get("/api/session")
    assert response.json() == {"authenticated": False, "username": None, "is_staff": False}
    assert "csrftoken" in response.cookies
    assert response["Cache-Control"] == "no-store"


def test_signing_in_and_out(client, groups):
    User.objects.create_user("pat", password=PASSWORD)
    signed_in = _post(client, "/api/session/login", {"username": "pat", "password": PASSWORD})
    assert signed_in.json() == {"authenticated": True, "username": "pat", "is_staff": False}
    # the same session cookie the chatbot page checks
    assert client.get("/chat/").status_code == 200
    assert client.get("/api/session").json()["username"] == "pat"

    signed_out = _post(client, "/api/session/logout")
    assert signed_out.json()["authenticated"] is False
    assert client.get("/chat/").status_code == 302


def test_clerks_are_marked_as_staff(client, clerk):
    assert client.get("/api/session").json() == {"authenticated": True, "username": "clerk", "is_staff": True}


def test_a_wrong_password_or_missing_fields_are_refused(client, groups):
    User.objects.create_user("pat", password=PASSWORD)
    wrong = _post(client, "/api/session/login", {"username": "pat", "password": "wrong"})
    assert wrong.status_code == 400 and "didn't match" in wrong.json()["error"]
    assert _post(client, "/api/session/login", {"username": "pat"}).status_code == 400
    assert client.post("/api/session/login", "not json", content_type="application/json").status_code == 400
    assert client.get("/api/session").json()["authenticated"] is False


def test_signing_in_needs_the_csrf_token(groups):
    User.objects.create_user("pat", password=PASSWORD)
    browser = Client(enforce_csrf_checks=True)
    refused = _post(browser, "/api/session/login", {"username": "pat", "password": PASSWORD})
    assert refused.status_code == 403
    token = browser.get("/api/session").cookies["csrftoken"].value
    allowed = _post(browser, "/api/session/login", {"username": "pat", "password": PASSWORD},
                    headers={"X-CSRFToken": token})
    assert allowed.status_code == 200


def test_only_get_for_who_am_i_and_only_post_to_sign_in_or_out(client):
    assert client.post("/api/session").status_code == 405
    assert client.get("/api/session/login").status_code == 405
    assert client.get("/api/session/logout").status_code == 405
