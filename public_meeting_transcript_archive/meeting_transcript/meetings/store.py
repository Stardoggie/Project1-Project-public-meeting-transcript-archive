from meeting_transcript.meetings.models import ListMeetingsDTO,Meetings,CreateMeetingsDTO,Status,UpdateMeetingsDTO
from pydantic import ValidationError,TypeAdapter
from meeting_transcript.extensions import db
from meeting_transcript.meetings.models_db import Meeting,Entities,KeyPhrases
from sqlalchemy import select
from sqlalchemy.orm import selectinload
from meeting_transcript.governing_body.models_db import GoverningBody
from datetime import datetime
import logging
from meeting_transcript.logging import log
from meeting_transcript.analysis.models import entityDTO,phraseDTO

statusAdapter = TypeAdapter(Status)
logger = logging.getLogger(__name__)

def list_meetings(body_id:int,status:str | None = None,start_date: str | None = None,end_date: str |None = None)->list[ListMeetingsDTO]: #also maybe work on meeting model names as well
    """
        lists all of the meetings relating to a specific governing body
        v2 has added filters
    """
    logger.debug("getting meetings")
    stmt = select(Meeting).where(Meeting.body_id == body_id).options(selectinload(Meeting.entities),selectinload(Meeting.keys))#finds relationship for meeting in other tables for selectinload
    if status is not None:
        valid_status = statusAdapter.validate_python(status)
        stmt = stmt.where(Meeting.status == valid_status)
    if start_date is not None:
        start_datetime = datetime.strptime(start_date,"%Y-%m-%d")
        stmt = stmt.where(Meeting.meeting_date >= start_datetime)
    if end_date is not None:
        end_datetime = datetime.strptime(end_date,"%Y-%m-%d")
        stmt = stmt.where(Meeting.meeting_date <= end_datetime)
    stmt = stmt.order_by(Meeting.id)
    rows = db.session.execute(stmt).scalars()
    #modified to return what is needed including if transcript available, and both the entitites and key_phraes
    return [ListMeetingsDTO(id=row.id,title=row.title,status=row.status,meeting_date=row.meeting_date,transcript_available=row.transcript_available,key_phrases=[
                key.phrase
                for key in row.keys
            ],
            entities=[
                entity.entity
                for entity in row.entities
            ])for row in rows]


def list_meeting(meeting_id:int)->ListMeetingsDTO | None:
    stmt = (select(Meeting).where(Meeting.id == meeting_id).options(selectinload(Meeting.entities),selectinload(Meeting.keys)))
    row = db.session.execute(stmt).scalar_one_or_none()
    logger.debug(row)
    if row is None:
        return None
    return ListMeetingsDTO(id=row.id,title=row.title,status=row.status,meeting_date=row.meeting_date,transcript_available=row.transcript_available,key_phrases=[
                    key.phrase
                    for key in row.keys
                ],
                entities=[
                    entity.entity
                    for entity in row.entities
                ])


def create_meeting(body_id:int, meeting:dict)-> Meetings:
    """
        creates a meeting for a specific governing body
    """
    valid_body = CreateMeetingsDTO.model_validate(meeting)
    valid_body.body_id = body_id
    record = Meeting(**valid_body.model_dump())
    db.session.add(record)
    db.session.commit()
    return Meetings.model_validate(record)


def edit_meeting(meeting_id:int, meeting:dict):
    """
        updates the meeting's title or meeting date
    """
    valid_body = UpdateMeetingsDTO.model_validate(meeting)
    record = db.session.get(Meeting,meeting_id)
    if record is None:
        return None
    record.title = valid_body.title
    record.meeting_date = valid_body.meeting_date
    db.session.commit()
    return Meetings.model_validate(record)


def delete_meeting(meeting_id:int):
    record = db.session.get(Meeting,meeting_id)
    if record is None:
        return False
    db.session.delete(record)
    db.session.commit()
    return True





