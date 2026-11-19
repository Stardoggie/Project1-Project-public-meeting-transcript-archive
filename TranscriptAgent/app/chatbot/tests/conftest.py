"""Point the chatbot's tools at the test database before tools.py creates its (read-only) Flask app."""
import os
import sys
from datetime import date
from pathlib import Path
import pytest
from dotenv import find_dotenv, load_dotenv
from sqlalchemy.engine import make_url
load_dotenv(find_dotenv(usecwd=True))
if not os.environ.get("DATABASE_TEST_URL"):
    pytest.exit("DATABASE_TEST_URL is not set; the chatbot tests need a separate test database.", returncode=1)
dev_url = os.environ.get("DATABASE_URL")
if dev_url and make_url(dev_url).database == make_url(os.environ["DATABASE_TEST_URL"]).database:
    pytest.exit("DATABASE_TEST_URL points at the same database as DATABASE_URL; use a separate test database.", returncode=1)
os.environ["DATABASE_URL"] = os.environ["DATABASE_TEST_URL"]
os.environ.setdefault("AWS_REGION", "us-east-1")
os.environ.setdefault("MEETING_BUCKET_NAME", "test-bucket")
# Let tests import the chatbot modules (graph, tools) the same way main.py does
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import tools  # noqa: E402
from meeting_transcript.app import create_app  # noqa: E402
from meeting_transcript.extensions import db  # noqa: E402
from meeting_transcript.governing_body.models_db import GoverningBody  # noqa: E402
from meeting_transcript.meetings.models_db import Meeting  # noqa: E402

# The chatbot's own app can't write, so tables and test data go in through an ordinary one
_writer = create_app()


@pytest.fixture(autouse=True)
def test_db():
    with _writer.app_context():
        db.create_all()
    yield
    with tools._flask.app_context():
        db.session.remove()
    with _writer.app_context():
        db.session.remove()
        db.drop_all()


@pytest.fixture
def archive():
    """A council with two meetings (one transcribed) and a board with one, written through the read-write app."""
    with _writer.app_context():
        council = GoverningBody(name="Springfield City Council", body="council", description="City council")
        board = GoverningBody(name="Parks Board", body="board", description="Parks and recreation")
        db.session.add_all([council, board])
        db.session.flush()
        budget = Meeting(title="Budget Hearing", meeting_date=date(2026, 3, 4), status="archived", body_id=council.id,
                         transcript="Good evening. The council discussed parking downtown and the 100% rise in fees. "
                                    "Councilmember Lee asked about Main Street parking again before the vote.")
        planned = Meeting(title="Regular Session", meeting_date=date(2026, 4, 1), body_id=council.id)
        parks = Meeting(title="Parks Review", meeting_date=date(2026, 2, 10), status="archived", body_id=board.id,
                        transcript="The board reviewed playground repairs and parking at Riverside Park.")
        db.session.add_all([budget, planned, parks])
        db.session.commit()
        return {"council": council.id, "board": board.id, "budget": budget.id, "planned": planned.id, "parks": parks.id}
