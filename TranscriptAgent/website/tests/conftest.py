"""Test setup: Django's own tables come from its migrations, the archive's tables from its SQLAlchemy models (they
belong to the archive, so Django never creates them), and both start empty for each test."""
import boto3
import pytest
from botocore.stub import Stubber
from django.contrib.auth.models import Group, User
from django.core.management import call_command
from meeting_transcript.extensions import db
from archive_site.archive import _app
from records.groups import CLERKS, VIEWERS
PASSWORD = "a-long-test-password-1"


@pytest.fixture(scope="session")
def django_db_setup(django_db_blocker):
    """Use the test database as it is (no test_ copy, since the archive's tables wouldn't be in it)."""
    with django_db_blocker.unblock():
        call_command("migrate", verbosity=0)


@pytest.fixture(autouse=True)
def archive_tables(transactional_db):
    """The archive's tables, made fresh for each test and dropped after it. transactional_db empties Django's."""
    with _app(False).app_context():
        db.create_all()
    yield
    for read_only in (False, True):
        with _app(read_only).app_context():
            db.session.remove()
    with _app(False).app_context():
        db.drop_all()


@pytest.fixture
def groups():
    call_command("setup_groups", verbosity=0)


@pytest.fixture
def clerk(client, groups):
    """A signed-in clerk: staff, in the Clerks group, not a superuser."""
    user = User.objects.create_user("clerk", password=PASSWORD, is_staff=True)
    user.groups.add(Group.objects.get(name=CLERKS))
    client.force_login(user)
    return user


@pytest.fixture
def viewer(client, groups):
    """A signed-in viewer: can use the chatbot page, not the admin."""
    user = User.objects.create_user("viewer", password=PASSWORD)
    user.groups.add(Group.objects.get(name=VIEWERS))
    client.force_login(user)
    return user


@pytest.fixture
def s3(monkeypatch):
    """A real S3 client with fake credentials whose API calls are checked by a Stubber instead of reaching AWS."""
    from records import recording
    client = boto3.client("s3", region_name="us-east-1", aws_access_key_id="testing", aws_secret_access_key="testing")
    monkeypatch.setattr(recording, "get_s3_client", lambda: client)
    with Stubber(client) as stubber:
        yield stubber
        stubber.assert_no_pending_responses()
