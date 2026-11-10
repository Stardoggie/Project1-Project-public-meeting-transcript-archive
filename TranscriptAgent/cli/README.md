# Terminal chat

Chat with the transcript agent from a terminal, and upload recordings by file path. It only uses Python's standard
library (Python 3.11+), so there is nothing to install.

## Run
1. Start the agent: `agentcore dev` in `TranscriptAgent/`.
2. In this folder: `python chat.py` (`python3 chat.py` on Linux and macOS)

The example below uses a Windows path; on Linux and macOS it would be `~/Recordings/hearing.mp3`.

```
archive> What meetings does Springfield City Council have?
archive> C:\Recordings\hearing.mp3 Budget Hearing, Springfield City Council
  Uploading hearing.mp3 (12.4 MB)... done.
archive> Check the transcription for Budget Hearing
archive> Show me the full transcript of Budget Hearing
archive> Delete the Budget Hearing meeting
  Please confirm: delete meeting "Budget Hearing" on 2026-01-05 (Springfield City Council, id 66) and its transcript and analysis. [y/N]
```

- **Recordings:** just give it the file's path, by typing it or dragging the file into the window, and it uploads
  the file itself and starts transcription; you never see an upload link. Say which meeting it's for in the same
  message ("...hearing.mp3 is for the zoning meeting") or earlier in the chat; otherwise the agent asks, and the
  file is uploaded once it knows. If the agent is ready for a recording before you've given a path, the CLI asks for
  it. The file must be MP3 or WAV, up to 500 MB, and is checked before anything is uploaded.
  Give the path on its own line: a pasted block, or a line with a link or command in it (like `curl -T file.mp3
  https://...`), is sent as text and never uploads anything.
- **Pasting:** a multi-line paste is sent as one message, not one per line. Lines of only punctuation (a stray
  backtick) are ignored.
- **Confirmations:** deletes (and replacing a meeting's transcribed audio) ask `[y/N]`; `y` or `yes` confirms, and
  Enter, anything else or Ctrl+D (end of input) cancels. Anything typed or pasted before the question appears is thrown
  away, so only what you type after it answers.
- **Full transcripts** are printed and saved as `<meeting> <date>.txt` in the current folder (`--transcripts DIR` to
  change it).
- `/new` starts a new conversation, `/help` lists commands, `/quit` exits.

## One-off use
```
python chat.py "Which meetings talked about parking?"
python chat.py --upload "C:\Recordings\hearing.mp3" --meeting "Budget Hearing, Springfield City Council"
python3 chat.py --upload ~/Recordings/hearing.mp3 --meeting "Budget Hearing, Springfield City Council"
```
The first upload line is for Windows, the second for Linux and macOS (`python3` there if `python` isn't found).
Without `--meeting`, the agent may ask which meeting the recording is for; the chat then stays open so you can
answer, and the file is uploaded once it knows.

The CLI finds `agentcore dev` on ports 8080–8089 by itself. Set `AGENT_URL` (or `--agent-url`) to use another
endpoint.

## Test
`python -m pytest test_chat.py` (offline; needs pytest).
