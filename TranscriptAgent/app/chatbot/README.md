# Archive chatbot

The read-only assistant on the archive's website, for signed-in viewers. It answers questions about governing bodies, meetings
and what was said in them, and names the meeting (title, governing body, date) behind every answer. It can't add,
change or delete anything; that's the clerks' agent's job (`../agent`, used from the terminal chat).

It's a LangGraph graph on Amazon Bedrock AgentCore Runtime, like the clerks' agent, using Claude Sonnet on Bedrock.

## Read-only
- Every tool only reads, so there is no confirmation step.
- Its Flask app is created with `create_app(read_only=True)` (`tools.py`), which stops writes twice over:
  - the app only sends `SELECT` and `SHOW`. Anything else, including a `SET` that could switch read-only off, is
    refused before it reaches the database;
  - every transaction it opens is read-only, so Postgres refuses writes anyway (for example `SELECT ... FOR UPDATE`).
- `tests/test_chatbot.py` checks both, and that only the read-only tools are offered.
- Its runtime's IAM policy (`meeting-transcript-public-agent`, copy in `iam/public-agent-policy.json`) only lets it
  read the database secret. It has no S3, Transcribe or Comprehend access, and it's denied the clerks' AgentCore
  Memory.

## Guardrail
A Bedrock Guardrail (`meeting-transcript-public-chatbot`, definition in `iam/chatbot-guardrail.json`) checks every
visitor's question and every answer:
- prompt attacks (attempts to override the chatbot's instructions) in questions
- hate, insults, sexual content, violence and misconduct: strictly in questions, at medium strength in answers,
  since answers can quote meetings that discuss crime
- off-limits topics in questions: voting advice and political opinions, and legal advice about someone's own
  situation. Answers aren't checked for these topics: a summary of a meeting that discusses a court case or an
  election would look like legal or voting advice to the topic filter, and be blocked
- sensitive numbers in answers (Social Security, tax, passport, driver's licence, card, bank, PIN and phone numbers)
  are replaced with a placeholder. People's names stay, since answers must say who said what.

A blocked question or answer is replaced with the guardrail's own message, and the chatbot logs that it happened
(without the visitor's text). The guardrail doesn't check what the tools return: transcripts are long and often
discuss crime or name people, which the filters would flag. Full transcripts shown with `show_full_transcript` go to
the visitor without passing through it.

`model/load.py` reads `GUARDRAIL_ID` and `GUARDRAIL_VERSION`. Settings that are only half there, that don't look like
a guardrail id, or that use `DRAFT` stop the chatbot from starting. The chatbot's IAM policy also refuses model calls
without a guardrail. To change the guardrail, update it in Bedrock, publish a new version, and set
`GUARDRAIL_VERSION` to it.

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

`{"recent_meetings": n}` on its own (n from 1 to 10) answers with one `{"type": "recent_meetings", "meetings": [...]}`
event: the n latest meetings dated today or earlier, newest first, with their governing body, status, whether a
transcript exists and a few key phrases. It reads the archive directly, without the model; the website's chatbot page
uses it for its recent meetings.

## Environment variables
| Variable | Required | Description |
| --- | --- | --- |
| `DATABASE_SECRET_ID` | One of these two | ARN or name of the Secrets Manager secret with the database's host, port, username, password and dbname. Deployed, this is `TranscriptionDBSecret` |
| `DATABASE_URL` | One of these two | Postgres URL; when set it's used instead of the secret |
| `AWS_REGION` | Yes | Region for Bedrock and Secrets Manager |
| `MEETING_BUCKET_NAME` | Yes | Read by the archive's config when it loads; the chatbot never uses the bucket |
| `GUARDRAIL_ID` | Deployed: yes | The Bedrock Guardrail's id or ARN. Leave it and `GUARDRAIL_VERSION` unset to run locally without one |
| `GUARDRAIL_VERSION` | Deployed: yes | A published version number of the guardrail, never `DRAFT` |

## Run and test locally
`agentcore dev --agent chatbot --port 8090` in `TranscriptAgent/` runs it, and the website
(`TranscriptAgent/website`, `DJANGO_DEBUG=1 uv run python manage.py runserver`) serves the page that talks to it, for
signed-in viewers.

Conversations are kept in the chatbot's own process memory, never in AgentCore Memory (that's where the clerks'
conversations are).

Tests: `uv run pytest tests` here, with `DATABASE_TEST_URL` pointing at a separate, local Postgres database.
