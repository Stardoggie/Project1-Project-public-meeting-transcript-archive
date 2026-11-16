"""CloudFormation custom resource (CDK Provider framework) that runs the archive's Alembic migrations.

It runs inside the VPC on every deploy that changes the archive code or its migrations, the same as
`flask --app meeting_transcript.app db upgrade` does locally. Upgrading is idempotent, and deleting the
stack leaves the database alone.
"""
import logging
import os

from flask_migrate import upgrade

from meeting_transcript.app import create_app

MIGRATIONS_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "migrations")
logging.getLogger().setLevel(logging.INFO)


def handler(event, context):
    physical_id = event.get("PhysicalResourceId") or "archive-migrations"
    if event["RequestType"] == "Delete":
        return {"PhysicalResourceId": physical_id}
    app = create_app()
    with app.app_context():
        upgrade(directory=MIGRATIONS_DIR)
    logging.info("Archive database is at the latest migration")
    return {"PhysicalResourceId": physical_id}
