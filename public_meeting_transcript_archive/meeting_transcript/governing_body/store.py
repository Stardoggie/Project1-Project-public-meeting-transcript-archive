from meeting_transcript.governing_body.models import * #will change to specifics once i have DTOs
from pydantic import ValidationError, TypeAdapter
from meeting_transcript.extensions import db
from meeting_transcript.governing_body.models_db import GoverningBody
from sqlalchemy import select,text,func
from meeting_transcript.meetings.models_db import *
from meeting_transcript.logging import log,logger


def list_bodies()-> list[ListGoverningBodyDTO]:
    """
        lists all of all of the governing bodies with counts
    """
    #                                 meeting count             labels it                        connects body with its meetings           count per body              order by lowest body ID -> top
    stmt = select(GoverningBody,func.count(Meeting.id).label("meeting_count")).outerjoin(Meeting,Meeting.body_id == GoverningBody.id).group_by(GoverningBody.id).order_by(GoverningBody.id)
    rows = db.session.execute(stmt)
    return [
        ListGoverningBodyDTO(
            id=body.id,
            name=body.name,
            body=body.body,
            description=body.description,
            meeting_count=meeting_count
        )for body, meeting_count in rows
    ]
    #note to self, might need to change gov db and model names slightly to not only use caps to differentiate

def list_body(body_id:int) -> governingBody:
    valid_body = db.session.get(GoverningBody,body_id)
    return governingBody.model_validate(valid_body)


def create_body(body:dict):
    """
        creates new governing bodies
    """
    valid_body = CreateGoverningBodyDTO.model_validate(body)
    record = GoverningBody(**valid_body.model_dump())
    #try to put record into db
    db.session.add(record)
    #commit it to db
    db.session.commit()
    return governingBody.model_validate(record)

def update_body(body_id:int,body:dict):
    """
        updates existing governing bodies
    """
    valid_body = UpdateGoverningBodyDTO.model_validate(body)
    record = db.session.get(GoverningBody,body_id)
    logger.debug(f"update record for body:{record}")
    if record is None:
        return None
    record.name = valid_body.name
    record.body = valid_body.body
    record.description = valid_body.description
    db.session.commit()
    return governingBody.model_validate(record)

def delete_body(body_id:int):
    """
        deletes a governing body of a given id
    """
    record = db.session.get(GoverningBody,body_id)
    if record is None:
        return False
    db.session.delete(record)
    db.session.commit()
    return True
    