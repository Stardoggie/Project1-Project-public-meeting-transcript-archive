"""The website's secret: Django reads the same pinned version CloudFront was deployed with."""
import json
from archive_site import settings


class FakeSecrets:
    """Stands in for Secrets Manager; records what it was asked for."""

    def __init__(self):
        self.asked = []

    def get_secret_value(self, **request):
        self.asked.append(request)
        return {"SecretString": json.dumps({"django_secret_key": "key", "origin_verify": "verify"})}


def test_the_pinned_version_is_read(monkeypatch):
    fake = FakeSecrets()
    monkeypatch.setattr(settings.boto3, "client", lambda *args, **kwargs: fake)
    monkeypatch.setenv("WEB_SECRET_ID", "TranscriptArchiveWebSecret")
    monkeypatch.setenv("WEB_SECRET_VERSION", "version-2")
    assert settings._web_secret()["origin_verify"] == "verify"
    monkeypatch.setenv("WEB_SECRET_VERSION", "")
    settings._web_secret()
    assert fake.asked == [{"SecretId": "TranscriptArchiveWebSecret", "VersionId": "version-2"},
                          {"SecretId": "TranscriptArchiveWebSecret"}]
