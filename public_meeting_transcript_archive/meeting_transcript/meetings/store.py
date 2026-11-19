from meeting_transcript.meetings.models import ListMeetingsDTO,Meetings,CreateMeetingsDTO,Status,UpdateMeetingsDTO,TranscriptMatchDTO
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


SNIPPET_RADIUS = 150  # characters kept on each side of a match
MAX_SNIPPETS = 3


def _snippets(transcript:str, query:str)->list[str]:
    """up to MAX_SNIPPETS excerpts around case-insensitive matches of query, without overlapping"""
    lowered, needle = transcript.lower(), query.lower()
    snippets, start = [], 0
    while len(snippets) < MAX_SNIPPETS:
        hit = lowered.find(needle, start)
        if hit < 0:
            break
        begin = max(0, hit - SNIPPET_RADIUS)
        end = min(len(transcript), hit + len(needle) + SNIPPET_RADIUS)
        snippet = transcript[begin:end].strip()
        snippets.append(("…" if begin > 0 else "") + snippet + ("…" if end < len(transcript) else ""))
        start = end
    return snippets


def search_transcripts(query:str, body_id:int | None = None, limit:int = 10)->list[TranscriptMatchDTO]:
    """
        meetings whose transcript contains query (case-insensitive), newest first, with excerpts around the matches
        body_id limits the search to one governing body
    """
    query = query.strip()
    if not query:
        return []
    #escape LIKE's wildcards so the text is matched literally
    pattern = "%" + query.replace("\\","\\\\").replace("%","\\%").replace("_","\\_") + "%"
    stmt = (select(Meeting).where(Meeting.transcript.ilike(pattern, escape="\\"))
            .options(selectinload(Meeting.body))
            .order_by(Meeting.meeting_date.desc(), Meeting.id.desc())
            .limit(max(1, min(limit, 25))))
    if body_id is not None:
        stmt = stmt.where(Meeting.body_id == body_id)
    rows = db.session.execute(stmt).scalars()
    return [TranscriptMatchDTO(id=row.id,title=row.title,governing_body=row.body.name,meeting_date=row.meeting_date,
                               snippets=_snippets(row.transcript,query)) for row in rows]


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


def edit_meeting(meeting_id:int, meeting:dict)->Meetings|None:
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


def delete_meeting(meeting_id:int)->bool:
    #lock first, so analysis results being stored land before the cascade loads the entities to delete
    record = db.session.get(Meeting,meeting_id,with_for_update=True,populate_existing=True)
    if record is None:
        return False
    db.session.delete(record)
    db.session.commit()
    return True





