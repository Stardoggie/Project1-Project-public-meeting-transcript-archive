from meeting_transcript.extensions import db
from meeting_transcript.analysis.models import CreateEntityDTO,CreatePhraseDTO
from meeting_transcript.meetings.models_db import Entities,KeyPhrases
from meeting_transcript.meetings.models_db import Meeting,Entities,KeyPhrases
from meeting_transcript.meetings.models import ListMeetingsDTO
from sqlalchemy import select,func


def split_transcripton_chunk(text:str,max_words=5000)->list:
    """
        split a transcript into smaller chunks to work with comprehend's limit
    """
    words = text.split()
    chunks = []
    #adding words into a chunck
    for word in range(0,len(words),max_words):
        chunk = " ".join(words[word:word+max_words])
        chunks.append(chunk)
    return chunks

def add_entities_and_phrases(key_phrases:set,entity_counts:dict,meeting_id:int)->None:
    """
        store entites and phrases in db
    """

        #comprehend is in a dictionary, so have to convert it into a usable dictionary
    for entity,count in entity_counts.items():
            entity_dto_data = {
                "entity": entity,
                "meeting_id": meeting_id,
                "entity_count": count
            }
            valid_entity = CreateEntityDTO.model_validate(entity_dto_data)
            record = Entities(entity=valid_entity.entity, meeting_id=valid_entity.meeting_id,entity_count=valid_entity.entity_count) #why this angry?
            db.session.add(record)
            #also converting to dict because it is easier than making new code
    for phrase in key_phrases:
            phrase_dto_data = {
                "phrase": phrase,
                "meeting_id": meeting_id
            }
            valid_phrase = CreatePhraseDTO.model_validate(phrase_dto_data)
            record = KeyPhrases(phrase=valid_phrase.phrase, meeting_id=valid_phrase.meeting_id)
            db.session.add(record)
    meeting = db.session.get(Meeting, meeting_id)
    if meeting is not None:
        meeting.analysis_completed = True

    db.session.commit()


def get_trending_entities(body_id:int)->dict:
      """
            get top 50 trending entities from a bodies meetings
      """
      stmt = select(Entities.entity).join(Meeting,Entities.meeting_id == Meeting.id).where(Meeting.body_id == body_id).group_by(Entities.entity).order_by(func.sum(Entities.entity_count).desc()).limit(50)
      rows = db.session.execute(stmt).scalars().all()
      return  {"trending-entities":rows}


def get_meeting_topic(body_id:int,phrase:str):
     """
        find meeting with a specific phrase in a specific body
     """
     stmt = (
         select(Meeting)
         .join(KeyPhrases, KeyPhrases.meeting_id == Meeting.id)
         .where((Meeting.body_id == body_id) & (KeyPhrases.phrase.ilike(f"%{phrase}%")))
         .distinct()
     )
     rows = db.session.execute(stmt).scalars().all()
     return [
        ListMeetingsDTO(
            id=row.id,
            title=row.title,
            status=row.status,
            meeting_date=row.meeting_date,
            transcript_available=row.transcript_available,
            key_phrases=[key.phrase for key in row.keys],
            entities=[entity.entity for entity in row.entities]
        )
        for row in rows
    ] 





    