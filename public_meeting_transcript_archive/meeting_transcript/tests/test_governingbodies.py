import pytest
from pydantic import ValidationError
from meeting_transcript.governing_body import store


def test_create_body(app):
    body = store.create_body({
        "name": "Test Governing Body",
        "body": "board",
        "description": "This is a test governing body."
    })
    assert body.id is not None
    assert body.name == "Test Governing Body"

def test_get_bodies(governing_body):
    bodies = store.list_bodies()
    assert [(b.id, b.meeting_count) for b in bodies] == [(governing_body.id, 0)]


def test_get_specific_body(governing_body):
    body = store.list_body(governing_body.id)
    assert body.name == "Test Governing Body"


def test_get_specific_body_not_found(app):
    assert store.list_body(999999) is None


def test_create_body_invalid(app):
    with pytest.raises(ValidationError):
        store.create_body({
            "name": "Test Governing Body",
            "body": "board"
            # missing desc
        })


def test_create_body_extra_field(app):
    with pytest.raises(ValidationError):
        store.create_body({
            "name": "Test Governing Body",
            "body": "board",
            "description": "test bad extra stuff.",
            "something_else": "not allowed"
        })


def test_update_body(governing_body):
    updated = store.update_body(governing_body.id,{
        "name": "Updated Governing Body",
        "body": "board",
        "description": "update the dec."
    })
    assert updated.name == "Updated Governing Body"
    assert updated.body == "board"


def test_update_body_not_found(app):
    assert store.update_body(999999,{
        "name": "Updated Governing Body",
        "body": "board",
        "description": "update the desc."
    }) is None


def test_delete_body(governing_body):
    assert store.delete_body(governing_body.id) is True
    assert store.list_body(governing_body.id) is None


def test_delete_body_not_found(app):
    assert store.delete_body(999999) is False


def test_a_short_description_written_before_the_rule_still_lists(governing_body):
    from meeting_transcript.extensions import db
    governing_body.description = "Old"  # written directly, as an older version or a hand edit could have
    db.session.commit()
    assert [body.description for body in store.list_bodies()] == ["Old"]
    assert store.list_body(governing_body.id).description == "Old"
    with pytest.raises(ValidationError):
        store.create_body({"name": "New", "body": "council", "description": "Old"})
