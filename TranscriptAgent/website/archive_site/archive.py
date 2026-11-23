"""Runs the archive's own store and service functions inside its Flask app context, the same way the agents' tools do,
so the website reuses the archive's checks and locking instead of copying them."""
from meeting_transcript.app import create_app
from meeting_transcript.extensions import db
# Made on first use, so importing this module never connects to the database
_apps = {}


def _app(read_only: bool):
    if read_only not in _apps:
        _apps[read_only] = create_app(read_only=read_only)
    return _apps[read_only]


def run(fn, *args, read_only=False, **kwargs):
    """Call fn in the archive's app context. read_only (the public API) uses the archive's read-only app, which only
    sends SELECT and SHOW. Errors are passed on, after rolling the session back."""
    with _app(read_only).app_context():
        try:
            return fn(*args, **kwargs)
        except Exception:
            db.session.rollback()
            raise


def dump(value):
    """The JSON-ready form of a store result: Pydantic models become dicts, lists are converted item by item."""
    if hasattr(value, "model_dump"):
        return value.model_dump(mode="json")
    if isinstance(value, list):
        return [dump(item) for item in value]
    return value
