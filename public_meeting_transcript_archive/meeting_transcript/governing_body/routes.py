from flask import Blueprint, jsonify, request
from meeting_transcript.governing_body.store import *
from meeting_transcript.responses import *
from pydantic import ValidationError



govbody_bp = Blueprint("governingBody",__name__)

@govbody_bp.get("")
def get_bodies(): #return later to add meeting count after meeting stuff is done
    """
        gets the governing bodies and displays them
    """
    return list_envelope_gov_with_count(list_bodies())

@govbody_bp.get("/<int:body_id>")
def get_specific_body(body_id:int): #return later to add meeting count after meeting stuff is done
    """
        gets the governing bodies and displays them
    """
    body = list_body(body_id=body_id)
    if body is None:
        return jsonify(error="not_found"), 404
    return single_envelope_gov(body)

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

@govbody_bp.put("/<int:body_id>")
def update_existing_body(body_id):
    body = request.get_json(silent=True) or {}
    output = update_body(body_id,body)
    if output is None:
        return jsonify(error="not found"),404
    return single_envelope_gov(output),200

@govbody_bp.delete("/<int:body_id>")
def delete_body_by_id(body_id):
    body = request.get_json(silent=True) or {}
    if body_id == body["id"]:
        success = delete_body(body_id)
        if success:
            return jsonify(status="deleted"),204
        return jsonify(error="not_found"),404
    return jsonify(error="bad_request",details="id needed in body"),400