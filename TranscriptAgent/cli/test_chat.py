"""Offline tests for the CLI's parsing and checks; no agent or AWS needed."""
import pytest

from chat import MAX_BYTES, check_recording, parse_upload, plain, safe_filename
from client import summarize


@pytest.mark.parametrize("rest, expected", [
    ('"C:\\My Recordings\\hearing.mp3" Budget Hearing, Springfield', ("C:\\My Recordings\\hearing.mp3", "Budget Hearing, Springfield")),
    ("'C:\\My Recordings\\hearing.mp3' Budget Hearing", ("C:\\My Recordings\\hearing.mp3", "Budget Hearing")),
    ("& 'C:\\My Recordings\\hearing.mp3' Budget Hearing", ("C:\\My Recordings\\hearing.mp3", "Budget Hearing")),
    ("C:\\rec\\hearing.wav Budget Hearing", ("C:\\rec\\hearing.wav", "Budget Hearing")),
    ("hearing.mp3", ("hearing.mp3", "")),
    ("", ("", "")),
])
def test_parse_upload(rest, expected):
    assert parse_upload(rest) == expected


def test_parse_upload_unclosed_quote():
    with pytest.raises(ValueError):
        parse_upload('"C:\\rec\\hearing.mp3 Budget Hearing')


def test_check_recording(tmp_path):
    good = tmp_path / "hearing.MP3"
    good.write_bytes(b"ID3")
    assert check_recording(str(good)) is None
    assert "not found" in check_recording(str(tmp_path / "missing.mp3"))
    other = tmp_path / "notes.txt"
    other.write_bytes(b"x")
    assert "MP3 and WAV" in check_recording(str(other))
    empty = tmp_path / "empty.wav"
    empty.write_bytes(b"")
    assert "empty" in check_recording(str(empty))
    big = tmp_path / "big.wav"
    with open(big, "wb") as f:
        f.truncate(MAX_BYTES + 1)  # sparse file, no real disk use
    assert "500 MB" in check_recording(str(big))


def test_plain_and_safe_filename():
    assert plain("**Budget Hearing** (id `66`)") == "Budget Hearing (id 66)"
    assert safe_filename('Budget: "Q3"/Q4? 2026-11-10') == "Budget_ _Q3__Q4_ 2026-11-10"
    assert safe_filename("...") == "transcript"


def test_summarize_keeps_replies_and_transcripts_in_order():
    events = [
        {"type": "ai", "text": "", "tool_calls": [{"name": "show_full_transcript", "args": {}}]},
        {"type": "tool", "tool": "show_full_transcript", "text": "note"},
        {"type": "transcript", "title": "T", "meeting_date": "2026-11-10", "transcript": "full text"},
        {"type": "ai", "text": "Shown above."},
        {"type": "confirmation_required", "question": "Please confirm: delete meeting 1."},
    ]
    result = summarize(events)
    assert [kind for kind, _ in result["items"]] == ["transcript", "reply"]
    assert result["tools"] == ["show_full_transcript"] and result["confirmation"] == "Please confirm: delete meeting 1."
