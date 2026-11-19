# Archive chatbot

The public, read-only assistant behind the archive's website. It answers questions about governing bodies, meetings
and what was said in them, and names the meeting (title, governing body, date) behind every answer. It can't add,
change or delete anything; that's the admin agent's job (`../agent`, used from the terminal chat).

It's a LangGraph graph on Amazon Bedrock AgentCore Runtime, like the admin agent, using Claude Sonnet on Bedrock.

## Read-only, twice over
- Its Flask app is created with `create_app(read_only=True)`, so every database transaction it opens is read-only and
  Postgres refuses writes (`tools.py`).
- Deployed, it logs in as `archive_reader`, a database user that can only `SELECT`. The deploy's migration step
  creates that user (`agentcore/cdk/lambda/migrate/handler.py`); its password is in its own Secrets Manager secret.

## Tools
| Tool | What it does |
| --- | --- |
| `list_governing_bodies`, `get_governing_body` | The councils, commissions and boards in the archive |
| `search_meetings` | One body's meetings, by date range, status or key phrase |
| `get_meeting` | A meeting's details, including its governing body, key phrases and entities |
| `search_transcripts` | Meetings whose transcript contains some text, with short excerpts |
| `get_transcript` | Reads a transcript for the model (truncated when very long) |
| `show_full_transcript` | Sends a whole transcript straight to the website, word for word |
| `get_trending_entities` | The people, places and organizations a body mentions most |

## Request
`POST /invocations` with `{"prompt": "..."}` and the `X-Amzn-Bedrock-AgentCore-Runtime-Session-Id` header (33+
characters; reuse it to continue a conversation). The response streams `data:` events: the model's replies, tool
calls and results, and `{"type": "transcript", ...}` for a full transcript.

## Environment variables
| Variable | Required | Description |
| --- | --- | --- |
| `DATABASE_SECRET_ID` | One of these two | ARN or name of the Secrets Manager secret with the database's host, port, username, password and dbname. Deployed, this is the `archive_reader` secret |
| `DATABASE_URL` | One of these two | Postgres URL; when set it's used instead of the secret |
| `AWS_REGION` | Yes | Region for Bedrock, Secrets Manager and AgentCore Memory |
| `MEETING_BUCKET_NAME` | Yes | Read by the archive's config when it loads; the chatbot never uses the bucket |
| `MEMORY_AGENTMEMORY_ID` | No | AgentCore Memory id (set by `agentcore deploy`). Unset: in-process conversation history |

## Run and test locally
`agentcore dev` in `TranscriptAgent/` runs it alongside the admin agent (see the root README). The web page talks to
it: `python server.py` in `TranscriptAgent/web/`.

Tests: `uv run pytest tests` here, with `DATABASE_TEST_URL` pointing at a separate, local Postgres database.
