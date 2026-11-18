from flask import Flask,json
from flask_migrate import Migrate
from meeting_transcript.extensions import db
from meeting_transcript.config import AppSettings,database_url
import logging
import os
migrate = Migrate()



class JsonFormatter(logging.Formatter):
    """
        a format for log calls using the format function
    """
    def format(self, record: logging.LogRecord) -> str:
        log_entry = {
            "timestamp": self.formatTime(record, "%Y-%m-%dT%H:%M:%S%z"),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage()
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

def create_app():
    '''
        uses factory pattern to create and return a new flask app
        the agent only uses it for the database context, and flask db upgrade for migrations
        there are no http routes, the agent calls the store and service functions directly
    '''
    configure_logging()
    app = Flask(__name__)
    app.config["SQLALCHEMY_DATABASE_URI"] = database_url()
    db.init_app(app)#initilizing db with flask
    migrate.init_app(app,db)#helps manage connection to db
    app.logger.info("Public Meeting Transcript Archive app created")
    return app
