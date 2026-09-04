from flask import Blueprint,jsonify,request
from meeting_transcript.meetings.store import *
from meeting_transcript.responses import *
from pydantic import ValidationError
import time
from meeting_transcript.logging import log


meetings_bp = Blueprint("meetings",__name__)

@meetings_bp.get("/<int:body_id>/meetings/")
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


@meetings_bp.post("/<int:body_id>/meetings")
def create_new_meetings(body_id:int):
    """
        creates a meeting for specified body
    """
    meeting = request.get_json(silent=True) or {}
    try:
        try:
            return single_envelope_meeting(create_meeting(body_id,meeting)),201
        except Exception as e:
            return jsonify(error="not_found",detail="governing body was not found"),404
    except ValidationError as e:
        return jsonify(error="Invalid Field",detail=e.errors()),400


@meetings_bp.put("/<int:body_id>/meetings/<int:meeting_id>")
def update_meetings(body_id,meeting_id):
    meeting = request.get_json(silent=True) or {}
    output = edit_meeting(meeting_id=meeting_id,meeting=meeting)
    if output is None:
        return jsonify(error="not found"),404
    return single_envelope_gov(output),200

@meetings_bp.delete("/<int:body_id>/meetings/<int:meeting_id>")
def delete_current_meeting(body_id,meeting_id):
    body = request.get_json(silent=True) or {}
    if body_id == body["id"]:
        success = delete_meeting(meeting_id=meeting_id)
        if success:
            return jsonify(status="deleted"),204
        return jsonify(error="not_found"),404
    return jsonify(error="bad_request",details="id needed in body"),400

@meetings_bp.get("/<int:body_id>/meetings/<int:meeting_id>")
def get_specific_meeting(body_id:int,meeting_id:int):
        meeting = list_meeting(meeting_id)
        if meeting is None:
            return jsonify(error="not_found"),404
        return single_envelope_meeting(list_meeting(meeting_id))
