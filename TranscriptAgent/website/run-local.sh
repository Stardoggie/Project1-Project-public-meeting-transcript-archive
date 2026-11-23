#!/bin/bash
# Starts everything for local testing in one go: the local Postgres, the database tables, the chatbot and the website.
# Ctrl+C stops the website and the chatbot together.
#
#   ./run-local.sh                                    website on 8000, chatbot on 8090
#   WEBSITE_PORT=8100 CHATBOT_PORT=8190 ./run-local.sh  other ports
#
# It always uses the local docker database, never the RDS database or its secret. Make your own sign-in once with
#   DATABASE_URL=postgresql+psycopg://postgres:root@localhost:5432/transcript_archive DJANGO_DEBUG=1 uv run python manage.py createsuperuser
set -e
WEBSITE_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_DIR="$(dirname "$WEBSITE_DIR")"
ARCHIVE_DIR="$(dirname "$PROJECT_DIR")/public_meeting_transcript_archive"
WEBSITE_PORT="${WEBSITE_PORT:-8000}"
CHATBOT_PORT="${CHATBOT_PORT:-8090}"
# The docker Postgres from public_meeting_transcript_archive/docker-compose.yml
export DATABASE_URL="postgresql+psycopg://postgres:root@localhost:5432/transcript_archive"
export DJANGO_DEBUG=1
export CHATBOT_URL="http://127.0.0.1:${CHATBOT_PORT}/invocations"
export CHATBOT_RUNTIME_ARN=""
CHATBOT_LOG="$WEBSITE_DIR/chatbot.log"

port_in_use() {
    ss -ltn | grep -q ":$1 "
}

stop_chatbot() {
    # The chatbot runs in its own process group, so this stops agentcore dev and the server it started
    if [ -n "$CHATBOT_GROUP" ]; then
        echo "Stopping the chatbot"
        kill -- "-$CHATBOT_GROUP" 2>/dev/null || true
    fi
}

if port_in_use "$WEBSITE_PORT"; then
    echo "Port $WEBSITE_PORT is already in use (a website still running?). Stop it, or set WEBSITE_PORT."
    exit 1
fi
echo "Starting the local Postgres"
(cd "$ARCHIVE_DIR" && docker compose up -d --wait db)
echo "Bringing the archive's tables up to date"
(cd "$ARCHIVE_DIR" && .venv/bin/flask --app meeting_transcript.app db upgrade)
echo "Bringing Django's tables and the Clerks and Viewers groups up to date"
cd "$WEBSITE_DIR"
uv sync -q
uv run python manage.py migrate -v0
uv run python manage.py setup_groups
if port_in_use "$CHATBOT_PORT"; then
    echo "Port $CHATBOT_PORT is already in use, so using the chatbot that's already there"
else
    echo "Starting the chatbot on port $CHATBOT_PORT (its output goes to chatbot.log)"
    setsid bash -c "cd '$PROJECT_DIR' && exec agentcore dev --agent chatbot --port $CHATBOT_PORT" > "$CHATBOT_LOG" 2>&1 &
    CHATBOT_GROUP=$!
    trap stop_chatbot EXIT
fi
echo "Website: http://127.0.0.1:$WEBSITE_PORT   (admin at /admin/, chatbot at /chat/, API at /api/v1/)"
uv run python manage.py runserver "127.0.0.1:$WEBSITE_PORT"
