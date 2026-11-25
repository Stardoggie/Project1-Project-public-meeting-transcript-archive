"""Refuses requests that didn't come through the site's CloudFront address."""
import hmac
from django.conf import settings
from django.http import HttpResponseForbidden


def require_cloudfront(get_response):
    """The Lambda's function URL is public, so CloudFront adds a secret X-Origin-Verify header to everything it sends
    on, and anything without it is refused. Locally there's no secret, so nothing is checked."""

    def middleware(request):
        expected = settings.ORIGIN_VERIFY
        if expected:
            sent = request.headers.get("X-Origin-Verify", "")
            if not hmac.compare_digest(sent.encode(), expected.encode()):
                return HttpResponseForbidden("Use the site's own address.")
        return get_response(request)

    return middleware
