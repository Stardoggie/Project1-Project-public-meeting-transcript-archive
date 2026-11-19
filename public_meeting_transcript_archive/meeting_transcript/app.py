from flask import Flask,json
from flask_migrate import Migrate
from meeting_transcript.extensions import db
from meeting_transcript.config import AppSettings, database_url
import logging
import re
from sqlalchemy import event
from sqlalchemy.exc import SQLAlchemyError
migrate = Migrate()
# The only statements a read-only app may send. Everything else is refused before it reaches the database,
# including SET, which could otherwise switch the connection's read-only default off.
_READ_STATEMENT = re.compile(r"\s*(SELECT|SHOW)\b", re.IGNORECASE)


class ReadOnlyError(SQLAlchemyError):
    """A read-only app tried to send a statement that isn't a query."""


def _refuse_non_queries(conn, cursor, statement, parameters, context, executemany):
    # Queries that still write (SELECT ... INTO, FOR UPDATE, nextval()) pass this check, but the read-only
    # transaction makes Postgres refuse them
    if not _READ_STATEMENT.match(statement):
        raise ReadOnlyError(f"read-only app refused a statement that isn't a query: {statement.split(None, 1)[0]}")



class JsonFormatter(logging.Formatter):
    """
        a format for log calls using the format function
    """
    def format(self, record: logging.LogRecord) -> str:
        log_entry = {
            "timestamp": self.formatTime(record, "%Y-%m-%dT%H:%M:%S%z"),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }

        if record.exc_info:
            log_entry["exception"] = self.formatException(record.exc_info)
        return json.dumps(log_entry)

def configure_logging() -> None:
    """
        sets up the config for logging
    """
    handler = logging.StreamHandler()
    handler.setFormatter(JsonFormatter())

    app_logger = logging.getLogger("meeting_transcript")
    app_logger.setLevel(AppSettings().log_level)
    app_logger.handlers.clear()
    app_logger.addHandler(handler)
    app_logger.propagate = False

def create_app(read_only: bool = False):
    '''
        uses factory pattern to create and return a new flask app
        the agent only uses it for the database context, and flask db upgrade for migrations
        there are no http routes, the agent calls the store and service functions directly
        read_only (the public chatbot uses this) stops writes twice over: the app only sends SELECT and SHOW,
        and every transaction on its connections is read-only, so the database itself refuses writes
    '''
    configure_logging()
    app = Flask(__name__)
    app.config["SQLALCHEMY_DATABASE_URI"] = database_url()
    if read_only:
        app.config["SQLALCHEMY_ENGINE_OPTIONS"] = {"connect_args": {"options": "-c default_transaction_read_only=on"}}
    db.init_app(app)#initilizing db with flask
    if read_only:
        with app.app_context():
            event.listen(db.engine, "before_cursor_execute", _refuse_non_queries)
    migrate.init_app(app,db)#helps manage connection to db
    app.logger.info("Public Meeting Transcript Archive app created")
    return app
