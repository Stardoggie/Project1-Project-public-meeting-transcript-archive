"""Signing in and out from the Vue frontend, as JSON.

The Django sign-in page (/accounts/login/) does the same with HTML forms; these let a Vue page do it with fetch.
Both use Django's own authenticate(), login() and logout(), so the session cookie and the rules are the same.

They change state, so they're kept apart from the public, read-only API under /api/v1/. Signing in and out need
Django's CSRF token in an X-CSRFToken header: GET /api/session sets the csrftoken cookie the page reads it from.
"""
import json
from django.contrib.auth import authenticate, login, logout
from django.http import JsonResponse
from django.views.decorators.csrf import ensure_csrf_cookie
from django.views.decorators.http import require_GET, require_POST


def _who(user) -> dict:
    """What the frontend needs to know about whoever is signed in, or that nobody is."""
    if not user.is_authenticated:
        return {"authenticated": False, "username": None, "is_staff": False}
    return {"authenticated": True, "username": user.get_username(), "is_staff": user.is_staff}


@require_GET
@ensure_csrf_cookie
def who_am_i(request):
    response = JsonResponse(_who(request.user))
    # Different for every visitor, so never cached
    response["Cache-Control"] = "no-store"
    return response


@require_POST
def sign_in(request):
    try:
        data = json.loads(request.body or b"{}")
    except ValueError:
        data = None
    if not isinstance(data, dict):
        return JsonResponse({"error": "Send a JSON object with a username and a password."}, status=400)
    username, password = data.get("username"), data.get("password")
    if not isinstance(username, str) or not isinstance(password, str) or not username or not password:
        return JsonResponse({"error": "Enter your username and password."}, status=400)
    user = authenticate(request, username=username, password=password)
    if user is None:
        return JsonResponse({"error": "That username and password didn't match. Try again."}, status=400)
    # Starts a new session (and a new CSRF token), exactly as the Django sign-in page does
    login(request, user)
    return JsonResponse(_who(user))


@require_POST
def sign_out(request):
    logout(request)
    return JsonResponse(_who(request.user))
