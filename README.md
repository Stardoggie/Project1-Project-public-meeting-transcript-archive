# Public Meeting Transcript Archive
An AI agent for an archive of public meeting transcripts. Clerks and residents talk to it in plain English: it manages
governing bodies and meetings, takes recordings, transcribes them with Amazon Transcribe, and finds people, places and
topics with Amazon Comprehend. The agent is a LangGraph graph running on Amazon Bedrock AgentCore.

## Layout
- `public_meeting_transcript_archive/meeting_transcript/`: the archive's storage and AWS logic (SQLAlchemy models, store and service functions, migrations). There is no REST API; the agent calls these functions directly.
- `TranscriptAgent/`: the AgentCore 1.0 project. The agent code is in `TranscriptAgent/app/agent/` (see its README).

## Install
### Requirements
- Python 3.11+, [uv](https://docs.astral.sh/uv/), Node.js 20.12+, the AgentCore CLI (`npm install -g @aws/agentcore`)
- AWS credentials (an AWS CLI profile) with the permissions in `iam/policy.json`, and Bedrock access to `us.anthropic.claude-sonnet-5-5`
- PostgreSQL 16+ (a local install, or `docker compose up -d db` in `public_meeting_transcript_archive/`)
- An S3 bucket for recordings and transcripts

### Settings
Put these in `TranscriptAgent/agentcore/.env.local` (loaded by `agentcore dev`):
```
DATABASE_URL=postgresql+psycopg://<user>:<password>@localhost:5432/transcript_archive
AWS_REGION=us-east-1
MEETING_BUCKET_NAME=<your bucket>
```
For the tests, also set `DATABASE_TEST_URL` to a separate database (for example in a `.env` at the repo root).

### Create the database tables
In `public_meeting_transcript_archive/`, with `DATABASE_URL` set:
```
pip install -e ".[dev]"
flask --app meeting_transcript.app db upgrade
```

## Run
```
cd TranscriptAgent
agentcore dev
```
Then send prompts to `POST http://localhost:8080/invocations` with `{"prompt": "..."}` and the header
`X-Amzn-Bedrock-AgentCore-Runtime-Session-Id: <any id>` (reuse it to continue a conversation).

## Test
- Archive logic: `pytest meeting_transcript/tests` in `public_meeting_transcript_archive/`
- Agent graph: `pytest tests` in `TranscriptAgent/app/agent/`

Both use `DATABASE_TEST_URL` and refuse to run against the `DATABASE_URL` database.

## Technology
- Python 3.14
- Amazon Bedrock AgentCore (Runtime, Memory) and the AgentCore CLI 1.0
- LangGraph and LangChain, with Claude Sonnet on Amazon Bedrock
- Amazon Transcribe
- Amazon Comprehend
- Amazon S3
- PostgreSQL 16.0+
- SQLAlchemy 2.0, Flask-SQLAlchemy and Flask-Migrate (database setup and migrations only)
- boto3
- pydantic
- pytest


## Edge Case Decisions
Asynchronous Transcription

### Transcription Doesn't Complete Synchronously:
  Transcribe is sent an audio file to work on. It returns a job key. The user asks the agent to check the transcription (check_transcription) to see if it is done.

### Transcribe Failure

  If a transcription fails, it is marked as recorded and will not be able to save as pending/failed. It is simple: just note that it failed and request the client to send another transcription attempt.

### Comprehend Failure

If Comprehend fails after a transcript has been created, just check the transcription again, and it will try again. This can be repeated until it is successful, marking it in the meeting table that the comprehending is completed.

### Long Transcripts

Long transcripts are split into smaller chunks, with 5000 words per chunk. Each chunk is made up of up to 5000 words in a string and appended into a list of chunks

### Unsupported or Corrupted Audio

Only MP3 and WAV recordings are accepted. The agent gives out a one-hour presigned S3 URL to upload the file to with an HTTP PUT; other file types are refused before a URL is made. The maximum size is 500MB, checked when the uploaded file is attached to a meeting. A corrupted file makes the Transcribe job fail, which is handled as a Transcribe failure.

### No Speech Detected

A completed transcription with a near-empty transcript is treated as a legitimate result rather than an application error. If it is empty, a note will be added to the transcript noting the lack of output. The transcript is stored, and no meaningful entities or key phrases are generated.

### Concurrent Mutations

The expected concurrent mutation is that if a governing body or meeting is deleted, then the transcription job will complete and not be stored. For two clerks editing, the last clerk will have the edit that will save, overwriting the older one.

### Governing Body Deletion

When a governing body is deleted, it deletes all of the meetings, entities, and key phrases underneath it. The agent pauses and asks the user to confirm every delete (and replacing a meeting's transcribed audio) before it runs.

### Editing After Transcription

Editing a meeting's title or meeting date does not retrigger transcription or Comprehend processing because the existing transcript was derived from the stored recording.
## ERD,policy, and kanban board
#### ERD
doc/erd.png
#### policy
iam/policy.json
#### kanban board
https://github.com/users/Stardoggie/projects/1
