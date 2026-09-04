from flask import jsonify
from meeting_transcript.governing_body.models import governingBody,ListGoverningBodyDTO
from meeting_transcript.meetings.models import *
from meeting_transcript.logging import log

class ApiError(Exception):
    """
        custom exception that can work with Flask's errorhandler()
    """
    def __init__(self,code: str,status: int, detail: str | None = None):
        #flask expects specific values for "code"
        # ex: "not found", "internal", "validation_failed"
        super().__init__(detail or code)
        self.code = code
        self.status = status
        self.detail = detail
#might need to split responses (lists) into the specific branches instead of global
# gov body envelopes

def list_envelope_gov(tickets: list[governingBody]):
     
     return jsonify(count=len(tickets),items=[t.model_dump(mode="json") for t in tickets])

def list_envelope_gov_with_count(tickets: list[ListGoverningBodyDTO]):
     
     return jsonify(count=len(tickets),items=[t.model_dump(mode="json") for t in tickets])

def single_envelope_gov(ticket: governingBody):
     return jsonify(ticket.model_dump(mode="json"))
#meetings envelopes

def list_envelope_meeting(tickets: list[ListMeetingsDTO]):
     
     return jsonify(count=len(tickets),items=[t.model_dump(mode="json") for t in tickets])

def single_envelope_meeting(ticket: Meetings):
     return jsonify(ticket.model_dump(mode="json"))
#error response

def error_response(code: str,status: int, detail: str | None = None):
    return jsonify(error=code,detail=detail),status

def single_envelope_payload(payload: dict):
    return jsonify(**payload)