"""Point the agent's tools at the test database before transcript_tools creates its Flask app."""
import os
import sys
from pathlib import Path
import pytest
from dotenv import find_dotenv, load_dotenv
from sqlalchemy.engine import make_url
load_dotenv(find_dotenv(usecwd=True))
if not os.environ.get("DATABASE_TEST_URL"):
    pytest.exit("DATABASE_TEST_URL is not set; the graph tests need a separate test database.", returncode=1)
dev_url = os.environ.get("DATABASE_URL")
if dev_url and make_url(dev_url).database == make_url(os.environ["DATABASE_TEST_URL"]).database:
    pytest.exit("DATABASE_TEST_URL points at the same database as DATABASE_URL; use a separate test database.", returncode=1)
os.environ["DATABASE_URL"] = os.environ["DATABASE_TEST_URL"]
os.environ.setdefault("AWS_REGION", "us-east-1")
os.environ.setdefault("MEETING_BUCKET_NAME", "test-bucket")
# Let tests import the agent modules (graph, transcript_tools) the same way main.py does
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import transcript_tools  # noqa: E402
from meeting_transcript.extensions import db  # noqa: E402


@pytest.fixture(autouse=True)
def test_db():
    with transcript_tools._flask.app_context():
        db.create_all()
    yield
    with transcript_tools._flask.app_context():
        db.session.remove()
        db.drop_all()
