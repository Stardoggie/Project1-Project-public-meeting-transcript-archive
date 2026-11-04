# Terminal chat

Chat with the transcript agent from a terminal, and upload recordings by file path. It only uses Python's standard
library (Python 3.11+), so there is nothing to install.

## Run
1. Start the agent: `agentcore dev` in `TranscriptAgent/`.
2. In this folder: `python chat.py`

```
archive> What meetings does Springfield City Council have?
archive> upload "C:\Recordings\hearing.mp3" Budget Hearing, Springfield City Council
archive> Check the transcription for Budget Hearing
archive> Show me the full transcript of Budget Hearing
archive> Delete the Budget Hearing meeting
  Please confirm: delete meeting 66 and its transcript and analysis. [y/N]
```

- **Upload:** `upload <path> <meeting>`. Quote paths with spaces, or drag the file into the window to paste its path.
  The file must be MP3 or WAV, up to 500 MB; it is checked before anything is uploaded. The CLI gets the upload link
  from the agent, uploads the file to S3, and has the agent attach it and start transcription.
- **Confirmations:** deletes (and replacing a meeting's transcribed audio) ask `[y/N]`; Enter cancels.
- **Full transcripts** are printed and saved as `<meeting> <date>.txt` in the current folder (`--transcripts DIR` to
  change it).
- `/new` starts a new conversation, `/help` lists commands, `/quit` exits.

## One-off use
```
python chat.py "Which meetings talked about parking?"
python chat.py --upload "C:\Recordings\hearing.mp3" --meeting "Budget Hearing, Springfield City Council"
```

The CLI finds `agentcore dev` on ports 8080–8089 by itself. Set `AGENT_URL` (or `--agent-url`) to use another
endpoint.

## Test
`python -m pytest test_chat.py` (offline; needs pytest).
