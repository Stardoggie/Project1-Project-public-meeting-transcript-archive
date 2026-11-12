"""Offline tests for the CLI's parsing and checks; no agent or AWS needed."""
import pytest
from chat import MAX_BYTES, Chat, check_recording, find_recording, plain, safe_filename
from client import summarize


@pytest.fixture
def recordings(tmp_path, monkeypatch):
    """A folder with a recording whose path has a space, and a bare one in the current folder."""
    folder = tmp_path / "My Recordings"
    folder.mkdir()
    spaced = folder / "hearing one.mp3"
    spaced.write_bytes(b"ID3")
    (tmp_path / "Zidon1.mp3").write_bytes(b"ID3")
    monkeypatch.chdir(tmp_path)
    return str(spaced)


def test_find_quoted_and_dragged_paths(recordings):
    assert find_recording(f'"{recordings}"') == (recordings, "")
    assert find_recording(f'"{recordings}" Budget Hearing, Springfield') == (recordings, "Budget Hearing, Springfield")
    assert find_recording(f"& '{recordings}'") == (recordings, "")
    assert find_recording(f'upload "{recordings}" for the zoning meeting') == (recordings, "for the zoning meeting")


def test_find_unquoted_paths_even_with_spaces(recordings):
    assert find_recording(f"{recordings} Budget Hearing") == (recordings, "Budget Hearing")
    assert find_recording(f"Add this to the Budget Hearing: {recordings}") == (recordings, "Add this to the Budget Hearing")
    assert find_recording("Zidon1.mp3 is for the Peace Alliance meeting") == ("Zidon1.mp3", "is for the Peace Alliance meeting")


def test_missing_paths_are_reported_but_plain_words_are_not(recordings):
    assert find_recording("C:\\nope\\missing.mp3 Budget Hearing") == ("C:\\nope\\missing.mp3", "Budget Hearing")
    assert find_recording('"C:\\docs\\notes.txt"') == ("C:\\docs\\notes.txt", "")
    assert find_recording("Which meeting had the mp3 player discussion?") == (None, "Which meeting had the mp3 player discussion?")
    assert find_recording("Tell me about notes.mp3") == (None, "Tell me about notes.mp3")
    assert find_recording("What meetings are there?") == (None, "What meetings are there?")


class FakeChat(Chat):
    """Records what would be sent and uploaded instead of calling the agent and S3."""

    def __init__(self, replies):
        super().__init__("http://agent.invalid")
        self.replies, self.sent, self.uploaded = list(replies), [], []

    def send(self, payload):
        self.sent.append(payload)
        result = summarize(self.replies.pop(0))
        if result["upload_url"]:
            return self.finish_upload(result["upload_url"], result["audio_key"])
        return result


UPLOAD_EVENTS = [{"type": "tool", "tool": "request_audio_upload", "text": '{"upload_url": "https://s3/put", "audio_key": "audio/meeting-transcription-x.mp3"}'}]
ATTACHED_EVENTS = [{"type": "ai", "text": "Attached and started transcription."}]


def test_a_path_with_its_meeting_uploads_straight_away(recordings, monkeypatch):
    chat = FakeChat([UPLOAD_EVENTS, ATTACHED_EVENTS])
    monkeypatch.setattr("chat.put_file", lambda url, path: chat.uploaded.append((url, path)) or 200)
    chat.handle(f'"{recordings}" Budget Hearing')
    assert chat.uploaded == [("https://s3/put", recordings)]
    assert "for this meeting: Budget Hearing" in chat.sent[0]["prompt"]
    assert chat.sent[1]["prompt"].startswith("The upload is done")


def test_a_path_alone_waits_for_the_agent_to_know_the_meeting(recordings, monkeypatch):
    asks = [{"type": "ai", "text": "Which meeting is this recording for?"}]
    chat = FakeChat([asks, UPLOAD_EVENTS, ATTACHED_EVENTS])
    monkeypatch.setattr("chat.put_file", lambda url, path: chat.uploaded.append((url, path)) or 200)
    chat.handle(f'"{recordings}"')
    assert chat.uploaded == [] and "meeting we've been talking about" in chat.sent[0]["prompt"]
    chat.handle("The Peace Alliance meeting")  # the agent now hands out the link; the file is still remembered
    assert chat.uploaded == [("https://s3/put", recordings)]


def test_agent_handing_out_a_link_asks_for_the_file_and_uploads_it(recordings, monkeypatch):
    chat = FakeChat([UPLOAD_EVENTS, ATTACHED_EVENTS])
    monkeypatch.setattr("chat.put_file", lambda url, path: chat.uploaded.append((url, path)) or 200)
    monkeypatch.setattr("builtins.input", lambda prompt: f"& '{recordings}'")
    chat.handle("I want to add a recording to the Peace Alliance meeting")
    assert chat.uploaded == [("https://s3/put", recordings)]


def test_cancelling_the_file_prompt_uploads_nothing(monkeypatch):
    chat = FakeChat([UPLOAD_EVENTS])
    monkeypatch.setattr("chat.put_file", lambda url, path: chat.uploaded.append(path) or 200)
    monkeypatch.setattr("builtins.input", lambda prompt: "")
    chat.handle("I want to add a recording to the Peace Alliance meeting")
    assert chat.uploaded == [] and len(chat.sent) == 1


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
        {"type": "confirmation_required", "question": "Please confirm: delete meeting 1."}
    ]
    result = summarize(events)
    assert [kind for kind, _ in result["items"]] == ["transcript", "reply"]
    assert result["tools"] == ["show_full_transcript"] and result["confirmation"] == "Please confirm: delete meeting 1."


def test_a_link_for_the_wrong_format_is_replaced_before_uploading(tmp_path, monkeypatch):
    wav = tmp_path / "hearing.wav"
    wav.write_bytes(b"RIFF")
    guessed = [{"type": "tool", "tool": "request_audio_upload",
                "text": '{"upload_url": "https://s3/put-mp3", "audio_key": "audio/meeting-transcription-a.mp3"}'}]
    matching = [{"type": "tool", "tool": "request_audio_upload",
                 "text": '{"upload_url": "https://s3/put-wav", "audio_key": "audio/meeting-transcription-b.wav"}'}]
    chat = FakeChat([guessed, matching, ATTACHED_EVENTS])
    monkeypatch.setattr("chat.put_file", lambda url, path: chat.uploaded.append(url) or 200)
    monkeypatch.setattr("builtins.input", lambda prompt: str(wav))
    chat.handle("I want to add a recording to the Peace Alliance meeting")
    assert chat.uploaded == ["https://s3/put-wav"]
    assert "hearing.wav" in chat.sent[1]["prompt"]


def _asks(text="Which meeting is this recording for?"):
    return [{"type": "ai", "text": text}]


def test_a_file_from_an_older_message_is_not_uploaded_without_asking(recordings, monkeypatch):
    chat = FakeChat([_asks(), _asks("Okay, I won't upload it."), UPLOAD_EVENTS])
    monkeypatch.setattr("chat.put_file", lambda url, path: chat.uploaded.append(path) or 200)
    answers = iter(["", ""])  # "Upload ... for this? [y/N]" -> Enter, then the path prompt -> Enter
    prompts = []
    monkeypatch.setattr("builtins.input", lambda prompt: prompts.append(prompt) or next(answers))
    chat.handle(f'"{recordings}"')
    chat.handle("Never mind, forget that recording")
    chat.handle("I'd like to add a different recording to the zoning meeting")
    assert chat.uploaded == []
    assert "the file you gave earlier" in prompts[0] and "Path to the recording" in prompts[1]


def test_a_file_from_an_older_message_is_uploaded_when_confirmed(recordings, monkeypatch):
    chat = FakeChat([_asks(), _asks("Sure."), UPLOAD_EVENTS, ATTACHED_EVENTS])
    monkeypatch.setattr("chat.put_file", lambda url, path: chat.uploaded.append(path) or 200)
    monkeypatch.setattr("builtins.input", lambda prompt: "y")
    chat.handle(f'"{recordings}"')
    chat.handle("Let me think about which meeting")
    chat.handle("It's for the zoning meeting")
    assert chat.uploaded == [recordings]


def test_pasted_text_and_commands_are_not_uploads(recordings, monkeypatch):
    chat = FakeChat([_asks("That looks pasted."), _asks("That's a command."), _asks("That's a link.")])
    monkeypatch.setattr("chat.put_file", lambda url, path: chat.uploaded.append(path) or 200)
    chat.handle(f'Here is your link.\ncurl.exe -X PUT -T "{recordings}" "<the URL above>"')
    chat.handle(f'curl.exe -X PUT -T "{recordings}" "https://bucket.s3.amazonaws.com/audio/x.mp3?X-Amz-Signature=1"')
    chat.handle(f"{recordings} https://example.com")
    assert chat.uploaded == [] and len(chat.sent) == 3
    assert chat.sent[0]["prompt"].startswith("Here is your link.\ncurl.exe")  # a paste goes as one message


def test_stray_punctuation_is_not_sent(monkeypatch):
    chat = FakeChat([])
    for junk in ("`", "``", "  `  ", "...", "[]"):
        chat.handle(junk)
    assert chat.sent == []


def test_new_conversation_forgets_the_file(recordings, monkeypatch):
    chat = FakeChat([_asks(), UPLOAD_EVENTS])
    monkeypatch.setattr("chat.put_file", lambda url, path: chat.uploaded.append(path) or 200)
    monkeypatch.setattr("builtins.input", lambda prompt: "")
    chat.handle(f'"{recordings}"')
    chat.handle("/new")
    chat.handle("I want to add a recording to the zoning meeting")
    assert chat.uploaded == []


def test_a_pasted_block_is_read_as_one_message_and_prompts_ignore_leftovers(monkeypatch):
    import console
    pasted = iter(["Here is your link.", "", "curl.exe -X PUT ...", "`"])
    buffered = [True, True, True, False]  # three more lines waiting after the first, then nothing
    monkeypatch.setattr(console, "_interactive", lambda: True)
    monkeypatch.setattr(console, "PASTE_SETTLE_SECONDS", 0)
    monkeypatch.setattr(console, "_waiting", lambda: buffered.pop(0))
    monkeypatch.setattr("builtins.input", lambda prompt="": next(pasted))
    assert console.read_message("archive> ") == "Here is your link.\n\ncurl.exe -X PUT ...\n`"
    discarded = []
    monkeypatch.setattr(console, "_discard", lambda: discarded.append(True))
    monkeypatch.setattr("builtins.input", lambda prompt="": "y")
    assert console.ask("Confirm? [y/N] ") == "y" and discarded == [True]


def test_apostrophes_do_not_break_path_detection(recordings):
    assert find_recording("Here's C:\\Recordings\\hearing.mp3 for the mayor's meeting") == (
        "C:\\Recordings\\hearing.mp3", "Here's for the mayor's meeting")
    assert find_recording(f"It's \"{recordings}\", for the mayor's meeting") == (recordings, "It's , for the mayor's meeting")
    assert find_recording("It's \"C:\\nope\\missing.mp3\", for the mayor's meeting")[0] == "C:\\nope\\missing.mp3"


def test_dragged_powershell_path_with_an_apostrophe(tmp_path):
    folder = tmp_path / "Bob's Recs"
    folder.mkdir()
    path = folder / "hearing.mp3"
    path.write_bytes(b"ID3")
    escaped = str(path).replace("'", "''")
    assert find_recording(f"& '{escaped}'") == (str(path), "")
    assert find_recording(f"& '{escaped}' Budget Hearing") == (str(path), "Budget Hearing")


def test_a_transcript_that_cant_be_saved_is_still_shown(tmp_path, capsys):
    blocker = tmp_path / "taken"
    blocker.write_text("a file, not a folder")
    chat = Chat("http://agent.invalid", str(blocker / "sub"))
    chat.show_transcript({"title": "Budget Hearing", "meeting_date": "2026-01-05", "transcript": "Mayor: Hello."})
    out = capsys.readouterr().out
    assert "Mayor: Hello." in out and "Couldn't save the transcript to" in out
    chat = Chat("http://agent.invalid", str(tmp_path / "new folder"))
    chat.show_transcript({"title": "Budget Hearing", "meeting_date": "2026-01-05", "transcript": "Mayor: Hello."})
    assert (tmp_path / "new folder" / "Budget Hearing 2026-01-05.txt").read_text(encoding="utf-8") == "Mayor: Hello."


def test_bare_quit_only_as_the_whole_line():
    chat = FakeChat([[{"type": "ai", "text": "The March meeting."}]])
    assert chat.handle("Exit polls: which meeting discussed them?") is True
    assert chat.sent == [{"prompt": "Exit polls: which meeting discussed them?"}]
    for line in ("quit", "  EXIT ", "/quit", "/exit now"):
        assert chat.handle(line) is False


def test_upload_without_a_meeting_stays_to_answer(recordings, monkeypatch):
    import sys
    import chat as chat_module
    loops = []
    monkeypatch.setattr(chat_module, "find_agent_url", lambda: "http://agent.invalid")
    monkeypatch.setattr(chat_module.Chat, "send", lambda self, payload: None)  # the agent asks which meeting
    monkeypatch.setattr(chat_module.Chat, "loop", lambda self: loops.append(self.pending_path))
    monkeypatch.setattr(sys, "argv", ["chat.py", "--upload", recordings])
    chat_module.main()
    assert loops == [recordings]
    loops.clear()
    monkeypatch.setattr(sys, "argv", ["chat.py", "--upload", recordings, "--meeting", "Budget Hearing"])
    monkeypatch.setattr(chat_module.Chat, "send", lambda self, payload: setattr(self, "pending_path", None))
    chat_module.main()
    assert loops == []


def test_a_path_at_the_end_of_a_sentence(recordings, tmp_path):
    hearing = tmp_path / "hearing.mp3"
    hearing.write_bytes(b"ID3")
    assert find_recording(f"Please upload {hearing}.") == (str(hearing), "Please upload")
    assert find_recording(f"{hearing}. It's for the zoning meeting.") == (str(hearing), "It's for the zoning meeting")
    assert find_recording("hearing.mp3.") == ("hearing.mp3", "")
    assert find_recording("C:\\nope\\missing.mp3.") == ("C:\\nope\\missing.mp3", "")
    assert find_recording("Tell me about notes.mp3.") == (None, "Tell me about notes.mp3.")


def test_a_bare_file_name_with_spaces(recordings, tmp_path):
    (tmp_path / "Budget Hearing 2024.mp3").write_bytes(b"ID3")
    name = "Budget Hearing 2024.mp3"
    assert find_recording(f"{name} for zoning") == (name, "for zoning")
    assert find_recording(f"Add {name} to zoning") == (name, "Add to zoning")
    assert find_recording(f"{name}. It's for the zoning meeting.") == (name, "It's for the zoning meeting")
    assert find_recording(f"Please upload {name}.") == (name, "Please upload")
    assert find_recording("Add Zoning Meeting 2024.mp3 to zoning") == (None, "Add Zoning Meeting 2024.mp3 to zoning")
    assert find_recording("I uploaded meeting.mp3 yesterday") == (None, "I uploaded meeting.mp3 yesterday")


def test_a_path_under_the_home_folder_is_expanded(tmp_path, monkeypatch):
    import sys
    import chat as chat_module
    (tmp_path / "Music").mkdir()
    real = tmp_path / "Music" / "hearing.mp3"
    real.write_bytes(b"ID3")
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("USERPROFILE", str(tmp_path))
    typed = "~\\Music\\hearing.mp3"
    assert find_recording(f"{typed} for zoning") == (typed, "for zoning")
    assert find_recording(f'"{typed}"') == (typed, "")
    chat = FakeChat([UPLOAD_EVENTS, ATTACHED_EVENTS])
    monkeypatch.setattr("chat.put_file", lambda url, path: chat.uploaded.append(path) or 200)
    chat.handle(f"{typed} for zoning")
    assert chat.uploaded == [str(real)] and "named hearing.mp3 for this meeting: for zoning" in chat.sent[0]["prompt"]
    chat = FakeChat([UPLOAD_EVENTS, ATTACHED_EVENTS])
    monkeypatch.setattr("chat.put_file", lambda url, path: chat.uploaded.append(path) or 200)
    monkeypatch.setattr("builtins.input", lambda prompt: typed)
    chat.handle("I want to add a recording to the zoning meeting")
    assert chat.uploaded == [str(real)]
    pending = []
    monkeypatch.setattr(chat_module, "find_agent_url", lambda: "http://agent.invalid")
    monkeypatch.setattr(chat_module.Chat, "send", lambda self, payload: None)
    monkeypatch.setattr(chat_module.Chat, "loop", lambda self: pending.append(self.pending_path))
    monkeypatch.setattr(sys, "argv", ["chat.py", "--upload", typed])
    chat_module.main()
    assert pending == [str(real)]


def test_a_single_quoted_path_with_an_apostrophe_inside(tmp_path):
    folder = tmp_path / "Recs"
    folder.mkdir()
    path = folder / "Bob's.mp3"
    path.write_bytes(b"ID3")
    assert find_recording(f"'{path}'") == (str(path), "")
    assert find_recording(f"'{path}' for the mayor's meeting") == (str(path), "for the mayor's meeting")
    assert find_recording("'C:\\nope\\Bob's.mp3'")[0] != "s.mp3"  # never the fragment after the apostrophe


def test_end_of_input_at_a_prompt_cancels_without_a_traceback(recordings, monkeypatch, capsys):
    def closed(prompt=""):
        raise EOFError
    monkeypatch.setattr("builtins.input", closed)
    confirmation = [{"type": "confirmation_required", "question": "Please confirm: delete meeting 3."}]
    chat = FakeChat([[{"type": "ai", "text": "Cancelled."}]])
    chat.confirm(summarize(confirmation))
    assert chat.sent == [{"confirm": False}]
    chat = FakeChat([UPLOAD_EVENTS])  # the path prompt ends too: nothing is uploaded
    monkeypatch.setattr("chat.put_file", lambda url, path: chat.uploaded.append(path) or 200)
    chat.handle("I want to add a recording to the zoning meeting")
    assert chat.uploaded == [] and "Not uploaded." in capsys.readouterr().out


def _agent_replies(monkeypatch, *responses):
    """Make the real Chat.send get these event lists from the agent, in order; returns what was sent."""
    queue, sent = list(responses), []
    monkeypatch.setattr("chat.invoke", lambda url, session, payload: sent.append(payload) or queue.pop(0))
    return sent


def test_server_errors_are_not_reported_as_unreachable(monkeypatch, capsys):
    import urllib.error

    def fail(url, session, payload):
        raise urllib.error.HTTPError(url, 500, "Internal Server Error", {}, None)
    monkeypatch.setattr("chat.invoke", fail)
    assert Chat("http://agent.invalid").send({"prompt": "hi"}) is None
    out = capsys.readouterr().out
    assert "HTTP 500 Internal Server Error" in out and "Can't reach" not in out

    def unreachable(url, session, payload):
        raise urllib.error.URLError("Connection refused")
    monkeypatch.setattr("chat.invoke", unreachable)
    Chat("http://agent.invalid").send({"prompt": "hi"})
    assert "Can't reach the agent" in capsys.readouterr().out


def test_a_reply_with_an_upload_link_is_still_shown_without_the_link(recordings, monkeypatch, capsys):
    link = [{"type": "ai", "text": "Ready for the recording at https://s3/put?X-Amz-Signature=abc"}, *UPLOAD_EVENTS,
            {"type": "confirmation_required", "question": "Please confirm: replace the recording for meeting 3."}]
    sent = _agent_replies(monkeypatch, link, [{"type": "ai", "text": "Okay."}], ATTACHED_EVENTS)
    uploaded = []
    monkeypatch.setattr("chat.put_file", lambda url, path: uploaded.append(path) or 200)
    monkeypatch.setattr("builtins.input", lambda prompt: "y")
    Chat("http://agent.invalid").handle(f'"{recordings}" Budget Hearing')
    out = capsys.readouterr().out
    assert "Ready for the recording at" in out and "https://s3/put" not in out
    assert sent[1] == {"confirm": True} and sent[2]["prompt"].startswith("The upload is done")
    assert uploaded == [recordings]


def test_markdown_upload_links_are_removed_whole(recordings, monkeypatch, capsys):
    link = [{"type": "ai", "text": "Ready: [upload](https://s3/put?X-Amz-Signature=abc) or <https://s3/put?sig=2>."},
            *UPLOAD_EVENTS]
    _agent_replies(monkeypatch, link, ATTACHED_EVENTS)
    monkeypatch.setattr("chat.put_file", lambda url, path: 200)
    Chat("http://agent.invalid").handle(f'"{recordings}" Budget Hearing')
    out = capsys.readouterr().out
    assert "Ready: or." in out and "[upload]" not in out and "s3/put" not in out and "<" not in out


def test_a_one_off_request_that_fails_exits_non_zero(monkeypatch):
    import sys
    import urllib.error
    import chat as chat_module

    def fail(url, session, payload):
        raise urllib.error.HTTPError(url, 500, "Internal Server Error", {}, None)
    monkeypatch.setattr(chat_module, "invoke", fail)
    monkeypatch.setattr(sys, "argv", ["chat.py", "--agent-url", "http://agent.invalid", "What meetings are there?"])
    with pytest.raises(SystemExit) as exited:
        chat_module.main()
    assert exited.value.code == 1
    monkeypatch.setattr(chat_module, "invoke", lambda url, session, payload: [{"type": "ai", "text": "None yet."}])
    chat_module.main()  # a request that gets through exits normally


def test_link_wrappers_go_with_the_link_and_punctuation_stays():
    from chat import without_link
    url = "https://s3/put?X-Amz-Signature=abc"
    assert without_link(f"Upload to `{url}`, then tell me.", url) == "Upload to, then tell me."
    assert without_link(f"Use **{url}**. Done", url) == "Use. Done"
    assert without_link(f"Send it to {url}, then wait.", url) == "Send it to, then wait."
    assert without_link(f"Here: **[upload]({url})**!", url) == "Here:!"
    assert without_link(f"Link: <{url}>; ok", url) == "Link:; ok"
    assert without_link("No link here, really.", url) == "No link here, really."


def test_upload_exits_non_zero_when_it_cant_go_ahead(tmp_path, monkeypatch, capsys):
    import sys
    import urllib.error
    import chat as chat_module
    good = tmp_path / "hearing.mp3"
    good.write_bytes(b"ID3")
    notes = tmp_path / "notes.txt"
    notes.write_text("x")
    loops = []
    monkeypatch.setattr(chat_module.Chat, "loop", lambda self: loops.append(True))

    def unreachable(url, session, payload):
        raise urllib.error.URLError("Connection refused")
    monkeypatch.setattr(chat_module, "invoke", unreachable)
    for path, message in [(str(good), "Can't reach the agent"), (str(tmp_path / "missing.mp3"), "File not found"),
                          (str(notes), "Only MP3 and WAV")]:
        monkeypatch.setattr(sys, "argv", ["chat.py", "--agent-url", "http://agent.invalid", "--upload", path])
        with pytest.raises(SystemExit) as exited:
            chat_module.main()
        assert exited.value.code == 1 and message in capsys.readouterr().out
    assert loops == []  # never dropped into the chat
