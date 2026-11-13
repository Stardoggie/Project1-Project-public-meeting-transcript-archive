# Public Meeting Transcript Archive
An AI agent for an archive of public meeting transcripts. Clerks and residents talk to it in plain English: it manages
governing bodies and meetings, takes recordings, transcribes them with Amazon Transcribe, and finds people, places and
topics with Amazon Comprehend. The agent is a LangGraph graph running on Amazon Bedrock AgentCore.

## Layout
- `public_meeting_transcript_archive/meeting_transcript/`: the archive's storage and AWS logic (SQLAlchemy models, store and service functions, migrations). There is no REST API; the agent calls these functions directly.
- `TranscriptAgent/`: the AgentCore 1.0 project. The agent code is in `TranscriptAgent/app/agent/` (see its README).

## Install
### Requirements
- Python 3.11+ (the deployed runtime uses 3.14), [uv](https://docs.astral.sh/uv/), Node.js 20.12+, the AgentCore CLI (`npm install -g @aws/agentcore`)
- AWS credentials (an AWS CLI profile) with the permissions in `iam/policy.json`, and Bedrock access to `us.anthropic.claude-sonnet-5-5`.
  `iam/policy.json` is a copy of the `meeting-transcript-archive` IAM policy, which the deployed runtime uses too.
- PostgreSQL 16+. The dev database is an RDS instance (`lg-transcription-db`) whose host, user and password are in the
  Secrets Manager secret `TranscriptionDBSecret`. Any other Postgres works too: a local install, or
  `docker compose up -d db` in `public_meeting_transcript_archive/` (the tests use this one).
- An S3 bucket for recordings and transcripts

### Settings
The archive finds its database in one of two ways (`database_url()` in `meeting_transcript/config.py`):
- `DATABASE_SECRET_ID`: the name of a Secrets Manager secret holding `host`, `port`, `username`, `password` and
  `dbname` (an "RDS database" secret has these). The archive reads it at startup and connects with SSL, so the password
  is never written in a file. This is how the RDS dev database and the deployed agent connect.
- `DATABASE_URL`: a full link, e.g. to a local Docker database. When it's set, it wins and the secret isn't read.

Put these in `TranscriptAgent/agentcore/.env.local` (loaded by `agentcore dev`):
```
DATABASE_SECRET_ID=TranscriptionDBSecret
AWS_REGION=us-east-1
MEETING_BUCKET_NAME=<your bucket>
```
`agentcore dev` is the only thing that reads `.env.local`. The migration and test commands below need the same
settings from your environment or from a `.env` at the repo root (which `meeting_transcript/config.py` loads), plus
`DATABASE_TEST_URL` pointing at a separate database for the tests (a local one; the tests never use the secret):
```
DATABASE_SECRET_ID=TranscriptionDBSecret
DATABASE_TEST_URL=postgresql+psycopg://<user>:<password>@localhost:5432/transcript_archive_test
AWS_REGION=us-east-1
MEETING_BUCKET_NAME=<your bucket>
```
To use a local database instead of RDS, replace `DATABASE_SECRET_ID` in both files with
`DATABASE_URL=postgresql+psycopg://<user>:<password>@localhost:5432/transcript_archive`.

### Create the database tables
In `public_meeting_transcript_archive/`, with the settings above (this creates the tables in whichever database they
point at, RDS or local):
```
pip install -e ".[dev]"
flask --app meeting_transcript.app db upgrade
```

## Run
Start the agent in one terminal:
```
cd TranscriptAgent
agentcore dev
```
Then chat with it from another, using the terminal chat (see `TranscriptAgent/cli/README.md`):
```
cd TranscriptAgent/cli
python chat.py
archive> C:\Recordings\hearing.mp3 Budget Hearing, Springfield City Council     (Windows)
archive> ~/Recordings/hearing.mp3 Budget Hearing, Springfield City Council       (Linux/macOS)
```
On Linux and macOS, use `python3` if `python` isn't found.
Other clients can send prompts to `POST http://localhost:8080/invocations` with `{"prompt": "..."}` and the header
`X-Amzn-Bedrock-AgentCore-Runtime-Session-Id: <any id>` (reuse it to continue a conversation; a request without it
starts a new conversation of its own).

## Deploy
`agentcore deploy` in `TranscriptAgent/` deploys the agent to AgentCore Runtime (account and region in
`agentcore/aws-targets.json`). The archive code is bundled with it. There is no stack code to write: the runtime's
settings are in `TranscriptAgent/agentcore/agentcore.json` (see the agent README), and it uses the existing
`TranscriptionDBSecret` secret, the `meeting-transcript-archive` IAM policy and the S3 bucket. Before the first deploy,
the RDS security group must allow port 5432 from anywhere, since the runtime has no fixed address (SSL and the
password still protect it).

Talk to the deployed agent with the terminal chat (needs boto3, which the archive's `.venv` has):
```
cd TranscriptAgent/cli
../../public_meeting_transcript_archive/.venv/bin/python chat.py --deployed
```
or send one request with `agentcore invoke --runtime agent --payload '{"prompt": "..."}' --session-id <33+ characters>`.
Logs: `agentcore logs runtime --name agent --since 15m`.

## Test
- Archive logic: `pytest meeting_transcript/tests` in `public_meeting_transcript_archive/`
- Agent graph: `uv run pytest tests` in `TranscriptAgent/app/agent/` (pytest is in its `dev` dependency group, which `uv sync` installs)

Both need the settings above, use `DATABASE_TEST_URL`, and refuse to run against the `DATABASE_URL` database.

## Technology
- Python (3.14 in the AgentCore runtime)
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


## Edge cases
### Transcription runs in the background
Transcribe doesn't finish right away. Starting transcription moves the meeting to `transcribing` and returns the job
name; ask the agent to check on it (`check_transcription`) and, once the job is done, the transcript is stored and
the meeting becomes `archived`.

### Transcribe fails
A failed job puts the meeting back to `recorded`, since the recording is still in S3, and the failure reason is
returned. Starting transcription again deletes the failed job and retries the same recording. If a finished job's
output has gone missing from S3, it is treated the same way.

### Comprehend fails
The transcript is stored before Comprehend runs, and the meeting is only marked analyzed once Comprehend succeeds.
If Comprehend fails, checking the transcription again retries the analysis until it works.

### Long transcripts
Comprehend has a size limit, so transcripts are split into chunks of up to 5,000 words and each chunk is analyzed
separately.

### Unsupported or corrupted audio
Only MP3 and WAV recordings are accepted. The agent hands out a one-hour signed S3 upload form (a presigned POST; the
CLI uploads the file with it by itself), and other file types are refused before a form is made. The form's signature
limits the file to 1 byte–500 MB and the matching audio type, so S3 itself refuses anything else before storing it.
Attaching checks the size again. A corrupted file makes the Transcribe job fail, which is handled as a Transcribe
failure.

### Where uploads go
Uploads land in `uploads/` in the bucket. Attaching one to a meeting moves it into `audio/`, where recordings are
kept. Anything still in `uploads/` (a cancelled, rejected or never-attached upload) is deleted after a day by an S3
lifecycle rule on the bucket (Management → Lifecycle rules: prefix `uploads/`, expire current versions after 1 day),
so nothing piles up. If attaching fails, the upload stays in `uploads/` until then, so attaching again still works.

### No speech detected
A finished job with an empty transcript is a valid result, not an error: it is stored as "No audio was detected." and
produces no entities or key phrases.

### Concurrent changes
If a meeting or governing body is deleted while its transcription is running, the finished transcript is simply not
stored. When two clerks edit the same meeting, the last save wins.

### Deleting a governing body
Deleting a governing body also deletes its meetings and their entities and key phrases. The recordings and transcript
files in S3, and the Transcribe jobs, are kept.

### Confirmations
The agent pauses and asks the user to confirm every delete, and replacing the recording of a meeting that already has
a transcript, before it runs. Only a plain "yes" confirms.

### Replacing a recording
Attaching new audio clears the meeting's transcript, entities and key phrases, so the new recording is transcribed and
analyzed from scratch.

### Editing after transcription
Editing a meeting's title or date doesn't rerun Transcribe or Comprehend, because the transcript came from the stored
recording.

## ERD, policy and kanban board
- ERD: `docs/ERD.png`
- IAM policy: `iam/policy.json`
- Kanban board: https://github.com/users/Stardoggie/projects/1
