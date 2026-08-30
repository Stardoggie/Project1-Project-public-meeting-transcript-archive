from flask import Blueprint, jsonify, request
from meeting_transcript.governing_body.store import *
from meeting_transcript.responses import *
from pydantic import ValidationError



govbody_bp = Blueprint("governingBody",__name__)

@govbody_bp.get("")
def get_bodies():
    """
        gets the governing bodies and displays them
    """
    return list_envelope_gov(list_bodies())

@govbody_bp.post("")
def create_new_body():
    """
        creates a new governing body
    """
    body = request.get_json(silent=True) or {}
    try:
        return single_envelope_gov(create_body(body)),201
    except ValidationError as e:
        return jsonify(error="Invalid Field",detail=e.errors()),400