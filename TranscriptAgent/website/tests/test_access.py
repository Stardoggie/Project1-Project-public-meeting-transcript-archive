"""Who can open what: visitors get the API only, viewers the chatbot, clerks the admin; and only via CloudFront."""
import json
from django.contrib.auth.models import Group, User
from django.test import override_settings
from chat import chatbot
from records.groups import VIEWERS
from tests.conftest import PASSWORD
TAB_ID = "web-0123456789abcdef0123456789abcdef"


def test_visitors_are_sent_to_sign_in(client):
    assert client.get("/").status_code == 200
    # the frontend's sign-in page, which comes back to the chatbot afterwards
    assert client.get("/chat/")["Location"] == "/signin?next=/chat/"
    assert client.get("/admin/")["Location"].startswith("/admin/login/")
    asked = client.post("/chat/ask", json.dumps({"prompt": "hi"}), content_type="application/json",
                        headers={"X-Session-Id": TAB_ID})
    assert asked.status_code == 401


def test_viewers_get_the_chatbot_but_not_the_admin(client, viewer):
    assert client.get("/chat/").status_code == 200
    assert client.get("/admin/")["Location"].startswith("/admin/login/")


def test_signing_in_with_a_password(client, groups):
    User.objects.create_user("pat", password=PASSWORD)
    signed_in = client.post("/accounts/login/", {"username": "pat", "password": PASSWORD})
    assert signed_in["Location"] == "/chat/"
    refused = client.post("/accounts/login/", {"username": "pat", "password": "wrong"})
    assert refused.status_code == 200 and b"didn't match" in refused.content


def test_passwords_can_be_changed_but_not_reset(client, viewer):
    # there's no email to send a reset link, so that page doesn't exist
    assert client.get("/accounts/password_reset/").status_code == 404
    assert client.get("/accounts/password_change/").status_code == 200


class FakeChatbot:
    """Stands in for the chatbot runtime: answers with one reply and records what it was sent."""

    def __init__(self):
        self.sent = []

    def events(self, session_id, payload):
        self.sent.append((session_id, payload))
        return [{"type": "ai", "text": "The council met on 2026-03-04."}], []


def test_a_question_goes_to_the_chatbot_in_the_users_own_session(client, viewer, monkeypatch):
    fake = FakeChatbot()
    monkeypatch.setattr(chatbot, "events", fake.events)
    response = client.post("/chat/ask", json.dumps({"prompt": "When did they meet?"}),
                           content_type="application/json", headers={"X-Session-Id": TAB_ID})
    answer = json.loads(b"".join(response.streaming_content))
    assert answer == {"items": [{"kind": "reply", "text": "The council met on 2026-03-04."}], "errors": []}
    assert fake.sent == [(f"user-{viewer.pk}-{TAB_ID}", {"prompt": "When did they meet?"})]


def test_a_question_needs_a_tab_id_and_a_prompt(client, viewer):
    assert client.post("/chat/ask", "{}", content_type="application/json").status_code == 400
    empty = client.post("/chat/ask", json.dumps({"prompt": " "}), content_type="application/json",
                        headers={"X-Session-Id": TAB_ID})
    assert empty.status_code == 400


def test_requests_that_skipped_cloudfront_are_refused(client):
    with override_settings(ORIGIN_VERIFY="from-cloudfront"):
        assert client.get("/api/v1/bodies").status_code == 403
        assert client.get("/api/v1/bodies", headers={"X-Origin-Verify": "guess"}).status_code == 403
        assert client.get("/api/v1/bodies", headers={"X-Origin-Verify": "from-cloudfront"}).status_code == 200


def test_clerks_only_manage_viewer_accounts(client, clerk):
    User.objects.create_superuser("owner", password=PASSWORD)
    listed = client.get("/admin/auth/user/")
    assert b"owner" not in listed.content
    # whatever the form says, a new account a clerk makes is a viewer
    made = client.post("/admin/auth/user/add/", {"username": "newviewer", "password1": PASSWORD,
                                                 "password2": PASSWORD, "usable_password": "true",
                                                 "is_staff": "on", "is_superuser": "on"})
    assert made.status_code == 302
    new_user = User.objects.get(username="newviewer")
    assert not new_user.is_staff and not new_user.is_superuser
    assert list(new_user.groups.all()) == [Group.objects.get(name=VIEWERS)]
