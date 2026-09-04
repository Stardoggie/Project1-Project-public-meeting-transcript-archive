"""
    routes for AWS transcribe
"""
from flask import Blueprint, request,jsonify
from meeting_transcript.responses import single_envelope_meeting,single_envelope_payload
from meeting_transcript.transcription import service
from meeting_transcript.uploads import read_upload
from meeting_transcript.transcription.store import add_audio,get_transcript
from meeting_transcript.transcription.service import start_transcription_job,get_transcription_job



UPLOAD_FILE = "file"
transcribe_bp = Blueprint("transcription",__name__)

#add audio to meeting
@transcribe_bp.post("/<int:body_id>/meetings/<int:meeting_id>")
def add_audio_to_meeting(body_id:int,meeting_id:int):
    audio,filename = read_upload(request.files.get(UPLOAD_FILE),allowed_extensions=service.ALLOWED_AUDIO_EXTENSIONS,max_bytes=service.MAX_AUDIO_BYTES)
    stored = add_audio(audio,filename,meeting_id=meeting_id,body_id=body_id)
    if stored is None:
        return jsonify(error="meeting_not_found"),404
    try:
        if stored["out"] == "out_of_bounds":
            return jsonify(error="Method_not_allowed",details="Meeting is not in governing body."),405
    except Exception:
        pass

    response = single_envelope_payload(stored)
    return response,202

@transcribe_bp.post("/<int:body_id>/meetings/<int:meeting_id>/transcribe")
def start_transcription(body_id:int,meeting_id:int):
    return start_transcription_job(meeting_id)

@transcribe_bp.post("/<int:body_id>/meetings/<int:meeting_id>/transcribe/status")
def get_transcription(body_id,meeting_id):
    #gets transcript from aws and stores it in db
    return get_transcription_job(meeting_id)

@transcribe_bp.get("/<int:body_id>/meetings/<int:meeting_id>/transcript")
def get_specific_transcript(body_id:int,meeting_id:int):
    try:
        transcript = get_transcript(meeting_id)
        if transcript is not None:
            return jsonify(transcript),200
    except Exception:
        pass

    return jsonify(error="transcript_not_found",detail="No transcript for this meeting!"),404
    