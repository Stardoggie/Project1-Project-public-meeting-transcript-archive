from meeting_transcript.extensions import db
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError
from meeting_transcript.logging import log



def ping() -> bool:
    return True


def live_ping()->bool:
    """
        tests if database is up by trying to query something basic
        if no query and timeout from db, then db not up (needs .env db URL to have timeout in it)
    """
    stmt = "SELECT 1"
    try:
            db.session.execute(text(stmt))
            return True
    
    except SQLAlchemyError as error:
        db.session.rollback()
        return False