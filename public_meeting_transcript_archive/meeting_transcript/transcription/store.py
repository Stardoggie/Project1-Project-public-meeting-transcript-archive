from meeting_transcript.meetings.models import ListMeetingsDTO,Meetings,CreateMeetingsDTO,Status,UpdateMeetingsDTO
from pydantic import ValidationError
from meeting_transcript.extensions import db
from meeting_transcript.meetings.models_db import Meeting
import logging
import uuid
logger = logging.getLogger(__name__)
from meeting_transcript.config import BUCKET_NAME
from meeting_transcript.aws.s3client import upload_audio

def _reset_analysis(record:Meeting)->None:
    """
        clears the old transcript and analysis so new audio gets transcribed and analyzed fresh
    """
    record.transcript = None
    record.transcribe_job_name = None
    record.analysis_completed = False
    #delete-orphan cascade removes the old rows
    record.entities.clear()
    record.keys.clear()

def add_audio(audio_bytes:bytes,filename:str,meeting_id:int,body_id:int)->dict |None:
    """
        this adds audio key to the db, it calls upload_audio() which stores the audio in s3
    """
    #takes in audio and places the audiokey in db while calling s3 to place it in 
    extension = filename.rsplit(".",1)[-1].lower()
    #generate unique job name for this file
    job_name = f"meeting-transcription-{uuid.uuid4().hex}"
    #putting the job name and extension together to create a unique file name
    audio_key = f"audio/{job_name}.{extension}"
    record = db.session.get(Meeting,meeting_id)
    if record is None:
        return None
    if record.body_id != body_id:
        return {"out":"out_of_bounds"}
    upload_audio(BUCKET_NAME,audio_key,audio_bytes)
    _reset_analysis(record)
    record.audio_object_key = audio_key
    record.status = "recorded"
    db.session.commit()
    return {"audio_key":audio_key}

def attach_audio(audio_key:str,meeting_id:int,body_id:int)->dict |None:
    """
        stores the key of audio already uploaded straight to s3 and marks the meeting recorded
        any earlier transcript and analysis are cleared
    """
    record = db.session.get(Meeting,meeting_id)
    if record is None:
        return None
    if record.body_id != body_id:
        return {"out":"out_of_bounds"}
    _reset_analysis(record)
    record.audio_object_key = audio_key
    record.status = "recorded"
    db.session.commit()
    return {"audio_key":audio_key}

def get_audio_key(meeting_id:int)->str:
    """
        gets the audio key for transcription from db
    """
    record = db.session.get(Meeting,meeting_id)
    audio_key = record.audio_object_key
    return audio_key

def store_transcription(meeting_id:int,transcription:str)-> None:
    """
        stores whole transcript in db
    """
    record  = db.session.get(Meeting,meeting_id)
    if len(transcription) < 1:
        transcription = "No audio was detected."
    record.transcript = transcription
    record.status = "archived"
    db.session.commit()

def get_transcript(meeting_id:int)->str|None:
    record = db.session.get(Meeting,meeting_id)
    if record.transcript_available:
        transcript = record.transcript
        return transcript
    return None

def store_job_key(meeting_id:int,job_key:str)->None:
    """
        stores AWS transcribe job key to call without having to input it into path
    """
    record = db.session.get(Meeting,meeting_id)
    record.transcribe_job_name = job_key
    record.status = "transcribing"
    db.session.commit()

def get_job_key(meeting_id:int)->str:
    """
        gets job key to use to find status of transcription
    """
    record = db.session.get(Meeting,meeting_id)
    job_key = record.transcribe_job_name
    return job_key

def revert_transcribe_failure(meeting_id:int)->None:
    """
        if transcribe failure then return status to recorded. audio is still stored after all
    """
    record = db.session.get(Meeting,meeting_id)
    record.status = "recorded"
    db.session.commit()

def analysis_check(meeting_id:int)->bool:
    record = db.session.get(Meeting,meeting_id)
    analysis = record.analysis_completed
    new_analysis = analysis
    db.session.commit()
    return new_analysis



