from meeting_transcript.meetings.models import *
from pydantic import ValidationError,TypeAdapter
from meeting_transcript.extensions import db
from meeting_transcript.meetings.models_db import Meeting,Entities,KeyPhrases
from sqlalchemy import select,text,func
from meeting_transcript.governing_body.models_db import GoverningBody
from datetime import date,datetime


statusAdapter = TypeAdapter(Status)

def list_meetings(body_id:int,status:str | None = None,start_date: str | None = None,end_date: str |None = None)->list[ListMeetingsDTO]: #also maybe work on meeting model names as well
    """
        lists all of the meetings relating to a specific governing body
        v2 has added filters
    """
    stmt = select(Meeting).where(Meeting.body_id == body_id)
    if status is not None:
        valid_status = statusAdapter.validate_python(status)
        stmt = stmt.where(Meeting.status == valid_status)
    if start_date is not None:
        start_datetime = datetime.strptime(start_date,"%Y-%m-%d")
        print(start_datetime)
        stmt = stmt.where(Meeting.meeting_date >= start_datetime)
    if end_date is not None:
        end_datetime = datetime.strptime(end_date,"%Y-%m-%d")
        stmt = stmt.where(Meeting.meeting_date <= end_datetime)
    stmt = stmt.order_by(Meeting.id)
    rows = db.session.execute(stmt).scalars()
    return [ListMeetingsDTO.model_validate(row) for row in rows]

def create_meeting(body_id:int, meeting:dict):
    """
        creates a meeting for a specific governing body
    """
    valid_body = CreateMeetingsDTO.model_validate(meeting)
    valid_body.body_id = body_id
    record = Meeting(**valid_body.model_dump())
    db.session.add(record)
    db.session.commit()
    return Meetings.model_validate(record)
