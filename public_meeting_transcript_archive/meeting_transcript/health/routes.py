from flask import Blueprint,jsonify
from meeting_transcript.health.store import ping,live_ping


liveliness_bp = Blueprint("health",__name__)

@liveliness_bp.get("/live") #empty string = defaulting to given url_prefix in app.py
def liveliness():
     if ping():
          return jsonify(status = "ok")
     return jsonify(status = "down"),500

@liveliness_bp.get("/ready")
def ready():
    """
          Confirm that the database is reachable.
    """
    if live_ping():
          return jsonify(status = "ok")
    return jsonify(status = "down"),500