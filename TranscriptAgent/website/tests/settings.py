"""Settings for the website's tests: always the local test database, never the deployed one or its secrets.
pytest-django loads these before any conftest runs, so the test database is chosen here."""
import os
from dotenv import find_dotenv, load_dotenv
from sqlalchemy.engine import make_url
load_dotenv(find_dotenv(usecwd=True))
if not os.environ.get("DATABASE_TEST_URL"):
    raise RuntimeError("DATABASE_TEST_URL is not set; the website tests need a separate test database.")
dev_url = os.environ.get("DATABASE_URL")
if dev_url and make_url(dev_url).database == make_url(os.environ["DATABASE_TEST_URL"]).database:
    raise RuntimeError("DATABASE_TEST_URL points at the same database as DATABASE_URL; use a separate test database.")
os.environ["DATABASE_URL"] = os.environ["DATABASE_TEST_URL"]
os.environ["DJANGO_SECRET_KEY"] = "tests-only-not-a-real-key"
os.environ["WEB_SECRET_ID"] = ""
os.environ["CHATBOT_RUNTIME_ARN"] = ""
os.environ.setdefault("AWS_REGION", "us-east-1")
os.environ.setdefault("MEETING_BUCKET_NAME", "test-bucket")
from archive_site.settings import *  # noqa: E402,F401,F403
# Tests never collect static files, so WhiteNoise has nothing to serve from
STATIC_ROOT = None
