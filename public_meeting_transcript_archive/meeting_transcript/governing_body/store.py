from meeting_transcript.governing_body.models import * #will change to specifics once i have DTOs
from pydantic import ValidationError, TypeAdapter
from meeting_transcript.extensions import db
from meeting_transcript.governing_body.models_db import GoverningBody
from sqlalchemy import select,text



def list_bodies()-> list[governingBody]:
    """
        lists all of all of the governing bodies
    """
    stmt = select(GoverningBody).order_by(GoverningBody.id)
    rows = db.session.execute(stmt).scalars()
    return[governingBody.model_validate(row) for row in rows] #note to self, might need to change gov db and model names slightly to not only use caps to differentiate

def create_body(body:dict):
    valid_body = CreateGoverningBodyDTO.model_validate(body)
    record = GoverningBody(**valid_body.model_dump())
    #try to put record into db
    db.session.add(record)
    #commit it to db
    db.session.commit()
    return governingBody.model_validate(record)