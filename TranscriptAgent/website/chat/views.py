"""The chatbot page, for signed-in viewers and clerks only. Visitors who aren't signed in get the read-only API."""
import functools
import json
import re
import threading
from django.contrib.auth.decorators import login_required
from django.http import JsonResponse, StreamingHttpResponse
from django.shortcuts import render
from . import chatbot
MAX_PROMPT_CHARS = 20_000
RECENT_MEETINGS = 3  # shown on the page when it opens
# A space this often while the chatbot works, so CloudFront's origin timeout doesn't cut a long answer off
KEEPALIVE_SECONDS = 10
# The page makes a random id per tab; the user's own id goes in front, so two people never share a conversation
TAB_ID = re.compile(r"[A-Za-z0-9-]{32,80}")


def _session_id(request) -> str | None:
    """The chatbot session for this user's tab (AgentCore wants 33 to 100 characters), or None if the id is bad."""
    tab_id = request.headers.get("X-Session-Id", "")
    if not TAB_ID.fullmatch(tab_id):
        return None
    return f"user-{request.user.pk}-{tab_id}"


def _checked(request) -> tuple[str | None, JsonResponse | None]:
    """The session id for a POST from the page, or the error to answer with."""
    if not request.user.is_authenticated:
        return None, JsonResponse({"errors": ["Sign in to use the chatbot."]}, status=401)
    if request.method != "POST":
        return None, JsonResponse({"errors": ["Not found"]}, status=405)
    session_id = _session_id(request)
    if session_id is None:
        return None, JsonResponse({"errors": ["Missing or invalid X-Session-Id header"]}, status=400)
    return session_id, None


@login_required
def page(request):
    return render(request, "chat/index.html", {"agent": chatbot.describe()})


def recent(request):
    session_id, refused = _checked(request)
    if refused:
        return refused
    return JsonResponse(chatbot.recent(session_id, RECENT_MEETINGS))


def _keep_alive_while(work):
    """Yields a space every few seconds until work() is done, then its answer as JSON (JSON.parse skips the spaces)."""
    answer = {}

    def run_work():
        answer["result"] = work()

    worker = threading.Thread(target=run_work, daemon=True)
    worker.start()
    while worker.is_alive():
        worker.join(KEEPALIVE_SECONDS)
        if worker.is_alive():
            yield " "
    yield json.dumps(answer.get("result", {"items": [], "errors": ["The chatbot stopped before it answered."]}))


def ask(request):
    session_id, refused = _checked(request)
    if refused:
        return refused
    try:
        prompt = json.loads(request.body or b"{}").get("prompt")
    except (ValueError, AttributeError):
        prompt = None
    if not isinstance(prompt, str) or not prompt.strip() or len(prompt) > MAX_PROMPT_CHARS:
        return JsonResponse({"errors": [f"Send a 'prompt' of up to {MAX_PROMPT_CHARS} characters"]}, status=400)
    answer = functools.partial(chatbot.ask, session_id, prompt)
    response = StreamingHttpResponse(_keep_alive_while(answer), content_type="application/json")
    response["Cache-Control"] = "no-store"
    return response
