"""CloudFormation custom resource (CDK Provider framework) that runs the archive's Alembic migrations.

It runs inside the VPC on every deploy that changes the archive code or its migrations, the same as
`flask --app meeting_transcript.app db upgrade` does locally. Upgrading is idempotent, and deleting the
stack leaves the database alone.

After migrating, it makes sure the chatbot's read-only database user exists (named and with the password in the
secret READER_SECRET_ID names) and can read every table, now and after future migrations, but change nothing.
"""
import json
import logging
import os

import boto3
from flask_migrate import upgrade
from psycopg import sql

from meeting_transcript.app import create_app
from meeting_transcript.config import AWS_REGION
from meeting_transcript.extensions import db

MIGRATIONS_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "migrations")
logging.getLogger().setLevel(logging.INFO)


def ensure_reader(secret_id: str):
    """Create or update the read-only user from its secret, and grant it SELECT on the archive's tables."""
    secret = json.loads(
        boto3.client("secretsmanager", region_name=AWS_REGION).get_secret_value(SecretId=secret_id)["SecretString"]
    )
    user = sql.Identifier(secret["username"])
    password = sql.Literal(secret["password"])
    # A raw psycopg connection, so names and the password are quoted by psycopg's sql module, never by formatting
    raw = db.engine.raw_connection()
    try:
        with raw.cursor() as cur:
            cur.execute("SELECT 1 FROM pg_roles WHERE rolname = %s", (secret["username"],))
            exists = cur.fetchone() is not None
            create_or_alter = sql.SQL("ALTER ROLE {} WITH LOGIN PASSWORD {}" if exists else
                                      "CREATE ROLE {} WITH LOGIN PASSWORD {}")
            cur.execute(create_or_alter.format(user, password))
            cur.execute("SELECT current_database()")
            database = sql.Identifier(cur.fetchone()[0])
            for statement in (
                "GRANT CONNECT ON DATABASE {database} TO {user}",
                "GRANT USAGE ON SCHEMA public TO {user}",
                "GRANT SELECT ON ALL TABLES IN SCHEMA public TO {user}",
                # tables later migrations create (as this admin user) are readable too
                "ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT SELECT ON TABLES TO {user}",
                # every session it opens is read-only, even if a grant were ever too broad
                "ALTER ROLE {user} SET default_transaction_read_only = on",
            ):
                cur.execute(sql.SQL(statement).format(database=database, user=user))
        raw.commit()
    finally:
        raw.close()
    logging.info("Read-only user %s is set up", secret["username"])


def handler(event, context):
    physical_id = event.get("PhysicalResourceId") or "archive-migrations"
    if event["RequestType"] == "Delete":
        return {"PhysicalResourceId": physical_id}
    app = create_app()
    with app.app_context():
        upgrade(directory=MIGRATIONS_DIR)
        logging.info("Archive database is at the latest migration")
        reader_secret = os.environ.get("READER_SECRET_ID")
        if reader_secret:
            ensure_reader(reader_secret)
    return {"PhysicalResourceId": physical_id}
