from meeting_transcript.analysis.service import comprehend_entities_and_phrases


def test_comprehend_entities_and_phrases(monkeypatch):
    class FakeComprehendClient:
        def detect_entities(self, **kwargs):
            return {
                "Entities": [
                    {
                        "Text": "Houston",
                        "Type": "LOCATION",
                        "Score": 0.99,
                    },
                    {
                        "Text": "John Smith",
                        "Type": "PERSON",
                        "Score": 0.99,
                    },
                ]
            }

        def detect_key_phrases(self, **kwargs):
            return {
                "KeyPhrases": [
                    {
                        "Text": "city council",
                        "Score": 0.99,
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

    def fake_add_entities_and_phrases(key_phrases, entities, meeting_id):
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

