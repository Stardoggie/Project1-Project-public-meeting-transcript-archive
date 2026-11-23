# Website

The archive's website, a Django project with three parts:

| Who | Where | What they can do |
| --- | --- | --- |
| Clerks (staff accounts) | `/admin/` | Add, edit and delete governing bodies and meetings; upload a meeting's recording and start or check its transcription; make viewer accounts |
| Viewers (accounts clerks make) | `/chat/` | Ask the read-only chatbot (`app/chatbot`) about the archive |
| Anyone, no sign-in | `/api/v1/` | Read the archive as JSON. No agent is involved, so it costs no Bedrock tokens |

There's no public sign-up: clerks make viewer accounts in the admin, under Users. A clerk can only make and change
viewer accounts; only a superuser can make clerks.

## How it fits with the archive
- **Unmanaged models.** `records/models.py` maps the archive's existing tables with `managed = False`. The
  archive's SQLAlchemy models and Alembic migrations still own those tables; keep the fields in step with
  `meeting_transcript/*/models_db.py` when they change.
- **One set of rules.** Every save and delete in the admin, and every API read, runs the archive's own store and
  service functions (`archive_site/archive.py`), the same code the clerks' agent uses. Uploads share
  `meeting_transcript/transcription/uploads.py` with the agent.
- **Read-only API.** The API only allows GET, and uses the archive's read-only app, which only sends SELECT and SHOW
  on read-only connections.
- **Django's own tables** (`auth_*`, `django_*`, for accounts and sign-ins) live in the same database. Alembic is set to
  ignore them (`include_archive_tables` in `public_meeting_transcript_archive/migrations/env.py`).

| Endpoint | Gives |
| --- | --- |
| `GET /api/v1/bodies` | Every governing body, with its meeting count |
| `GET /api/v1/bodies/<id>` | One governing body |
| `GET /api/v1/bodies/<id>/meetings?status=&start_date=&end_date=&phrase=` | A body's meetings, filtered by status (`scheduled`, `recorded`, `transcribing`, `archived`), dates (YYYY-MM-DD) or a key phrase |
| `GET /api/v1/bodies/<id>/trending-entities` | The people, places and organizations a body mentions most |
| `GET /api/v1/meetings/<id>` | One meeting, with its key phrases and entities |
| `GET /api/v1/meetings/<id>/transcript` | A meeting's full transcript |
| `GET /api/v1/search?q=<text>&body_id=<id>` | Meetings whose transcript contains the text, with short excerpts |

## Run it locally
The quick way, in this folder:
```
./run-local.sh
```
It starts the local docker Postgres, brings the archive's tables, Django's tables and the Clerks and Viewers groups up to
date, starts the chatbot on port 8090 (its output goes to `chatbot.log`) and runs the website on
http://127.0.0.1:8000. It always uses the local database, never RDS or its secret. Ctrl+C stops the website and the
chatbot together. If something is already on 8090 it uses that chatbot; other ports:
`WEBSITE_PORT=8100 CHATBOT_PORT=8190 ./run-local.sh`.

Make your own sign-in once (it asks for a password):
```
DATABASE_URL=postgresql+psycopg://postgres:root@localhost:5432/transcript_archive DJANGO_DEBUG=1 uv run python manage.py createsuperuser
```

Step by step instead: it uses the repo's `.env`. Point `DATABASE_URL` at a local Postgres with the archive's tables
(`flask --app meeting_transcript.app db upgrade` in `public_meeting_transcript_archive/`), then in this folder:
```
uv sync
DJANGO_DEBUG=1 uv run python manage.py migrate
DJANGO_DEBUG=1 uv run python manage.py setup_groups
DJANGO_DEBUG=1 uv run python manage.py createsuperuser
DJANGO_DEBUG=1 uv run python manage.py runserver
```
Then open http://127.0.0.1:8000. For the chatbot page, also start the chatbot in `TranscriptAgent/` with
`agentcore dev --agent chatbot --port 8090`. `DJANGO_DEBUG=1` is for local work only: it uses a throwaway secret key,
so sign-ins last until the server restarts.

After changing the archive's code, run `uv sync --reinstall-package public-meeting-transcript-archive` here, since the
website holds its own copy of the archive.

## Deployed
`agentcore deploy` in `TranscriptAgent/` builds the site into a Python Lambda (with the AWS Lambda Web Adapter running
gunicorn, so long chatbot answers stream) behind CloudFront. The stack's `SiteUrl` output is its address. The deploy
builds the Lambda's code on your machine with uv, so no Docker is needed.

Before the first deploy, make the website's own secret in Secrets Manager. The name must be exactly
`TranscriptArchiveWebSecret`, and it holds two random values:
```
aws secretsmanager create-secret --region us-east-1 --name TranscriptArchiveWebSecret --secret-string "{\"django_secret_key\": \"$(openssl rand -hex 32)\", \"origin_verify\": \"$(openssl rand -hex 32)\"}"
```
- `django_secret_key` signs sign-in cookies and form tokens.
- `origin_verify`: CloudFront adds it to every request it sends to the Lambda, and Django refuses requests without
  it. The Lambda's address is public, so this is what stops anyone from skipping CloudFront.

CloudFront and Django both read one version of the secret: `WEB_SECRET_VERSION` in
`TranscriptAgent/agentcore/cdk/lib/cdk-stack.ts`. Set it to the `VersionId` that `create-secret` printed. To change
the values later:
```
aws secretsmanager put-secret-value --region us-east-1 --secret-id TranscriptArchiveWebSecret --secret-string "{\"django_secret_key\": \"$(openssl rand -hex 32)\", \"origin_verify\": \"$(openssl rand -hex 32)\"}"
```
then put the new `VersionId` it prints into `WEB_SECRET_VERSION` and run `agentcore deploy`. The site keeps using the
old version until that deploy, so changing the secret on its own never breaks it. (Left empty, both read the current
version, but a deploy doesn't notice when the secret changes, so CloudFront would keep the old value while new
Lambdas read the new one, and requests would start being refused.) Changing `django_secret_key` signs everyone out.

The Lambda uses the existing `meeting-transcript-archive` IAM policy (S3, Transcribe, Comprehend, the database
secret), plus read access to the website's secret and permission to call the chatbot.

After the first deploy:
1. **Accounts:** with `lg-transcription-db` running, run `migrate`, `setup_groups` and `createsuperuser` against it
   (as above, but with the repo's `.env` using `DATABASE_SECRET_ID` instead of `DATABASE_URL`, and without
   `DJANGO_DEBUG`, with `DJANGO_SECRET_KEY` set to anything). Then sign in at `SiteUrl/admin/` as the superuser,
   make each clerk a user with "Staff status" in the Clerks group, and let clerks make viewers.
2. **Uploads:** the admin's recording page sends files straight from the browser to S3, so add a CORS rule to
   `lg-transcription-bucket` for the site's address (copy in `iam/bucket-cors.json`; put the `SiteUrl` in it first):
   ```
   aws s3api put-bucket-cors --bucket lg-transcription-bucket --cors-configuration file://iam/bucket-cors.json
   ```

## Test
`uv run pytest` here, with `DATABASE_TEST_URL` in the repo's `.env` pointing at a separate, local Postgres database
(`tests/settings.py` always uses it, never `DATABASE_URL` or any secret). S3, Transcribe and the chatbot are faked.
