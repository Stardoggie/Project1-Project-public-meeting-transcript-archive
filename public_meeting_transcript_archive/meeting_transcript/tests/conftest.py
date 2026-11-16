import pytest
from meeting_transcript.app import create_app
from meeting_transcript.extensions import db
from meeting_transcript.governing_body.models_db import GoverningBody
from meeting_transcript.meetings.models_db import Meeting
from meeting_transcript.meetings import store as meetings_store
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
        TESTING=True
    )
    with app.app_context():
        db.create_all()
        yield app
        db.session.remove()
        db.drop_all()
        #each test builds a new engine, so close its pooled connections
        db.engine.dispose()


@pytest.fixture
def governing_body(app):
    body = GoverningBody(
        name="Test Governing Body",
        body="council",
        description="This is a test governing body."
    )
    db.session.add(body)
    db.session.commit()
    body_id = body.id
    yield body
    #a test may have deleted it already
    record = db.session.get(GoverningBody,body_id)
    if record is not None:
        db.session.delete(record)
        db.session.commit()

@pytest.fixture
def meeting(governing_body):
    created = meetings_store.create_meeting(governing_body.id,{
        "title": "Test Meeting",
        "meeting_date": "2026-09-03"
    })
    return created.model_dump(mode="json")