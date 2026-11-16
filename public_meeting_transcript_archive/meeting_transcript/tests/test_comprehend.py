from meeting_transcript.analysis.service import comprehend_entities_and_phrases


def test_comprehend_entities_and_phrases(monkeypatch):
    class FakeComprehendClient:
        def detect_entities(self, **kwargs):
            return {
                "Entities": [
                    {
                        "Text": "Houston",
                        "Type": "LOCATION",
                        "Score": 0.99
                    },
                    {
                        "Text": "John Smith",
                        "Type": "PERSON",
                        "Score": 0.99
                    }
                ]
            }

        def detect_key_phrases(self, **kwargs):
            return {
                "KeyPhrases": [
                    {
                        "Text": "city council",
                        "Score": 0.99
                    }
                ]
            }

    monkeypatch.setattr(
        "meeting_transcript.analysis.service.get_comprehend_client",
        lambda: FakeComprehendClient()
    )
    monkeypatch.setattr(
        "meeting_transcript.analysis.service.split_transcripton_chunk",
        lambda text: [text]
    )
    captured = {}

    def fake_add_entities_and_phrases(key_phrases, entities, meeting_id, job_key=None):
        captured["key_phrases"] = key_phrases
        captured["entities"] = entities
        captured["meeting_id"] = meeting_id

    monkeypatch.setattr(
        "meeting_transcript.analysis.service.add_entities_and_phrases",
        fake_add_entities_and_phrases
    )
    comprehend_entities_and_phrases(
        "The city council met in Houston with John Smith.",
        1
    )
    assert captured["meeting_id"] == 1
    assert captured["entities"]["houston"] == 1
    assert captured["entities"]["john smith"] == 1
    assert "city council" in captured["key_phrases"]


class LongEntityComprehendClient:
    def detect_entities(self, **kwargs):
        return {
            "Entities": [
                {"Text": "B" * 151, "Type": "ORGANIZATION", "Score": 0.999},
                {"Text": "Jane Doe", "Type": "PERSON", "Score": 0.999}
            ]
        }

    def detect_key_phrases(self, **kwargs):
        return {"KeyPhrases": []}


def test_comprehend_skips_entities_over_150_chars(monkeypatch):
    monkeypatch.setattr(
        "meeting_transcript.analysis.service.get_comprehend_client",
        lambda: LongEntityComprehendClient()
    )
    captured = {}
    monkeypatch.setattr(
        "meeting_transcript.analysis.service.add_entities_and_phrases",
        lambda key_phrases, entities, meeting_id, job_key=None: captured.update(entities=entities)
    )
    comprehend_entities_and_phrases("Jane Doe spoke.", 1)
    assert captured["entities"] == {"jane doe": 1}


def test_comprehend_long_entity_is_stored_without_error(meeting, monkeypatch):
    from meeting_transcript.extensions import db
    from meeting_transcript.meetings.models_db import Entities, Meeting
    monkeypatch.setattr(
        "meeting_transcript.analysis.service.get_comprehend_client",
        lambda: LongEntityComprehendClient()
    )
    comprehend_entities_and_phrases("Jane Doe spoke.", meeting["id"])
    stored = db.session.query(Entities.entity).filter_by(meeting_id=meeting["id"]).all()
    assert [row.entity for row in stored] == ["jane doe"]
    assert db.session.get(Meeting, meeting["id"]).analysis_completed is True


def test_key_phrase_search_escapes_like_wildcards(meeting, governing_body):
    from datetime import date
    from meeting_transcript.analysis.store import get_meeting_topic
    from meeting_transcript.extensions import db
    from meeting_transcript.meetings.models_db import KeyPhrases, Meeting
    other = Meeting(title="Other", meeting_date=date(2026, 9, 4), body_id=governing_body.id)
    db.session.add(other)
    db.session.flush()
    db.session.add_all([
        KeyPhrases(phrase="budget increase", meeting_id=meeting["id"]),
        KeyPhrases(phrase="a 5% tax", meeting_id=other.id),
        KeyPhrases(phrase="zoning_code update", meeting_id=other.id),
        KeyPhrases(phrase="docs\\share folder", meeting_id=other.id)
    ])
    db.session.commit()

    def ids(phrase):
        return sorted(m.id for m in get_meeting_topic(governing_body.id, phrase))

    assert ids("%") == [other.id]
    assert ids("_") == [other.id]
    assert ids("5%") == [other.id]
    assert ids("BUDGET") == [meeting["id"]]
    assert ids("tax") == [other.id]
    assert ids("budget%increase") == []
    assert ids("budget_increase") == []
    # a literal backslash matches only phrases that contain one
    assert ids("docs\\share") == [other.id]
    assert ids("s\\") == [other.id]
    assert ids("\\") == [other.id]
    assert ids("t\\") == []
    assert ids("\\%") == []
    assert ids("docs\\_share") == []


class PaddedPhraseComprehendClient:
    def __init__(self, phrases):
        self.phrases = phrases

    def detect_entities(self, **kwargs):
        return {"Entities": []}

    def detect_key_phrases(self, **kwargs):
        return {"KeyPhrases": [{"Text": text, "Score": 0.999} for text in self.phrases]}


def test_key_phrase_is_stored_stripped_and_lowercased(meeting, monkeypatch):
    from meeting_transcript.extensions import db
    from meeting_transcript.meetings.models_db import KeyPhrases
    # 150 chars once stripped, 152 with the padding
    core = "A" * 74 + " " + "B" * 75
    # 150 chars stripped, but lowercasing the dotted I makes it longer than 150
    grows = "İ" * 74 + " " + "b" * 75
    monkeypatch.setattr(
        "meeting_transcript.analysis.service.get_comprehend_client",
        lambda: PaddedPhraseComprehendClient([" " + core + " ", grows, "  City Council  "])
    )
    comprehend_entities_and_phrases("text", meeting["id"])
    stored = sorted(row.phrase for row in db.session.query(KeyPhrases.phrase).filter_by(meeting_id=meeting["id"]))
    assert stored == sorted([core.lower(), "city council"])


def test_add_entities_and_phrases_skips_deleted_meeting(app):
    from meeting_transcript.analysis.store import add_entities_and_phrases
    from meeting_transcript.extensions import db
    from meeting_transcript.meetings.models_db import Entities, KeyPhrases
    add_entities_and_phrases({"city council"}, {"houston": 1}, 999999)
    assert db.session.query(Entities).filter_by(meeting_id=999999).count() == 0
    assert db.session.query(KeyPhrases).filter_by(meeting_id=999999).count() == 0

