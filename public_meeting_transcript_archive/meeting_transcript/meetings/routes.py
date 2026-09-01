from flask import Blueprint,jsonify,request
from meeting_transcript.meetings.store import *
from meeting_transcript.responses import *
from pydantic import ValidationError
import time



meetings_bp = Blueprint("meetings",__name__)

@meetings_bp.get("/<int:body_id>")
def get_meetings(body_id:int):
    """
        gets meetings of a specific governing body id
    """
    status = request.args.get("status")
    start_date = request.args.get("date-from") #dates to be refined when actually make a meeting to test
    end_date = request.args.get("date-to")

    if status is None and start_date is None and end_date is None:
        return list_envelope_meeting(list_meetings(body_id))
    return list_envelope_meeting(list_meetings(body_id,status=status,start_date=start_date,end_date=end_date))

@meetings_bp.post("/<int:body_id>")
def create_new_meetings(body_id:int):
    """
        creates a meeting for specified body
    """
    meeting = request.get_json(silent=True) or {}
    try:
        return single_envelope_meeting(create_meeting(body_id,meeting)),201
    except ValidationError as e:
        return jsonify(error="Invalid Field",detail=e.errors()),400
