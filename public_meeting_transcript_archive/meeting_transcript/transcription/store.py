from meeting_transcript.meetings.models import ListMeetingsDTO,Meetings,CreateMeetingsDTO,Status,UpdateMeetingsDTO
from pydantic import ValidationError
from meeting_transcript.extensions import db
from meeting_transcript.meetings.models_db import Meeting
import logging
logger = logging.getLogger(__name__)

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

def meeting_exists(meeting_id:int)->bool:
    """
        true if a meeting with this id is in the db
    """
    return db.session.get(Meeting,meeting_id) is not None

def meeting_not_found()->dict:
    """
        error returned when the meeting id doesn't exist
    """
    return {"error":"not_found","detail":"Meeting not found."}

def get_audio_key(meeting_id:int)->str:
    """
        gets the audio key for transcription from db
    """
    record = db.session.get(Meeting,meeting_id)
    audio_key = record.audio_object_key
    return audio_key

def store_transcription(meeting_id:int,transcription:str)-> str|None:
    """
        stores whole transcript in db and returns the text that was stored, None if the meeting is gone
    """
    record  = db.session.get(Meeting,meeting_id)
    if record is None:
        return None
    if len(transcription) < 1:
        transcription = "No audio was detected."
    record.transcript = transcription
    record.status = "archived"
    db.session.commit()
    return transcription

def get_transcript(meeting_id:int)->str|dict|None:
    """
        the meeting's transcript, None if it has none, or a not found error if the meeting doesn't exist
    """
    record = db.session.get(Meeting,meeting_id)
    if record is None:
        return meeting_not_found()
    if record.transcript_available:
        transcript = record.transcript
        return transcript
    return None

def store_job_key(meeting_id:int,job_key:str)->None:
    """
        stores AWS transcribe job key to call without having to input it into path
    """
    record = db.session.get(Meeting,meeting_id)
    if record is None:
        return
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
    if record is None:
        return
    record.status = "recorded"
    db.session.commit()

def already_transcribed(meeting_id:int,job_key:str)->bool:
    """
        true if the meeting's transcript was already stored from this job
    """
    record = db.session.get(Meeting,meeting_id)
    return record is not None and record.transcript is not None and record.transcribe_job_name == job_key

def analysis_check(meeting_id:int)->bool|None:
    """
        whether the meeting was analyzed, None if the meeting is gone
    """
    record = db.session.get(Meeting,meeting_id)
    if record is None:
        return None
    analysis = record.analysis_completed
    new_analysis = analysis
    db.session.commit()
    return new_analysis



