import pytest

from meeting_transcript.app import create_app
from meeting_transcript.extensions import db
from meeting_transcript.governing_body.models_db import GoverningBody
from meeting_transcript.meetings.models_db import Meeting
import os
from sqlalchemy.engine import make_url

@pytest.fixture
def app(monkeypatch):
    #create_app builds the engine from DATABASE_URL, so point it at the test db first
    test_url = os.environ["DATABASE_TEST_URL"]
    dev_url = os.environ.get("DATABASE_URL")
    #drop_all runs after each test, so never let it touch the dev db
    if dev_url:
        assert make_url(test_url).database != make_url(dev_url).database, "DATABASE_TEST_URL points at the same database as DATABASE_URL; use a separate test database."
    monkeypatch.setenv("DATABASE_URL",test_url)
    app = create_app()

    app.config.update(
        TESTING=True,
    )

    with app.app_context():
        db.create_all()
        yield app
        db.session.remove()
        db.drop_all()


@pytest.fixture
def client(app):
    return app.test_client()

@pytest.fixture
def governing_body(app):
    body = GoverningBody(
        name="Test Governing Body",
        body="council",
        description="This is a test governing body.",
    )

    db.session.add(body)
    db.session.commit()

    yield body

    db.session.delete(body)
    db.session.commit()

    pytest.fixture
@pytest.fixture
def meeting(client, governing_body):
    response = client.post(
        f"/api/v1/governing-bodies/{governing_body.id}/meetings",
        json={
            "title": "Test Meeting",
            "meeting_date": "2026-09-03",
        },
    )

    assert response.status_code == 201

    return response.get_json()