from meeting_transcript.responses import *
from pydantic import ValidationError
from meeting_transcript.analysis.store import *
from flask import Blueprint, request, jsonify
from meeting_transcript.responses import list_envelope_meeting
from meeting_transcript.meetings.store import list_meetings



analysis_bp = Blueprint("analysis",__name__)


@analysis_bp.get("/<int:body_id>/trending-entities")
def trending_entities(body_id:int):
    return (get_trending_entities(body_id))


#had an issue with this overlapping with list tickets, added part of list tickets here. list tickets still in older place in case of killing of transcribe and comprehend
@analysis_bp.get("/<int:body_id>/meetings")
def get_meetings_with_filters(body_id):
    phrase = request.args.get("key-phrase")
    status = request.args.get("status")
    start_date = request.args.get("start-date")
    end_date = request.args.get("end-date")

    if phrase:
        result = get_meeting_topic(body_id=body_id,phrase=phrase)
    else:
        result = list_meetings(body_id=body_id,status=status,start_date=start_date,end_date=end_date)

    return list_envelope_meeting(result)
    