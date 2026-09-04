# Public Meeting Transcript Archive
## Install
### In the project folder after creating .venv: <br> 
pip install -e .       

## Run
docker compose up --build

## Technology
- Python 3.14
- Flask 3.0
- Amazon Transcribe
- Amazon Comprehend
- Amazon S3
- Docker
- PostgreSQL 16.0+
- Sqlalchemy 2.0
- boto3
- pydantic
- pytest


## Edge Case Decisions
Asynchronous Transcription

### Transcription Doesn't Complete Synchronously:
  Transcribe is sent an audio file to work on. It returns a job key. The client will poll on trancribe/status to see if transcription is done.

### Transcribe Failure

  If a transcription fails, it is marked as recorded and will not be able to save as pending/failed. It is simple: just note that it failed and request the client to send another transcription attempt.

### Comprehend Failure

If Comprehend fails after a transcript has been created, just run get_transcription, and it will try again. This can be repeated until it is successful, marking it in the meeting table that the comprehending is completed.

### Long Transcripts

Long transcripts are split into smaller chunks, with 5000 words per chunk. Each chunk is made up of up to 5000 words in a string and appended into a list of chunks

### Unsupported or Corrupted Audio

Only MP3 and WAV recordings are accepted. Unsupported formats return HTTP 422. The maximum upload size is 500MB.

### No Speech Detected

A completed transcription with a near-empty transcript is treated as a legitimate result rather than an application error. If it is empty, a note will be added to the transcript noting the lack of output. The transcript is stored, and no meaningful entities or key phrases are generated.

### Concurrent Mutations

The expected concurrent mutation is that if a governing body or meeting is deleted, then the transcription job will complete and not be stored. For two clerks editing, the last clerk will have the edit that will save, overwriting the older one.

### Governing Body Deletion

When a governing body is deleted, it deletes all of the meetings, entities, and key phrases underneath it.

### Editing After Transcription

Editing a meeting's title or meeting date does not retrigger transcription or Comprehend processing because the existing transcript was derived from the stored recording.
