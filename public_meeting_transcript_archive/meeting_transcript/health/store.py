from meeting_transcript.extensions import db
from flask import jsonify
from sqlalchemy import select,text
from sqlalchemy.exc import SQLAlchemyError

def ping() -> bool:
    return True


def live_ping()->bool:
    stmt = "SELECT 1"
    try:
            db.session.execute(text(stmt))
            return True
    
    except SQLAlchemyError as error:
        db.session.rollback()
        return False