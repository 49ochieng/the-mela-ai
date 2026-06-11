"""Static security response headers.

Applied to every response leaving the API. Defaults are tuned for a
JSON-only API consumed by the Next.js frontend; CSP is intentionally
strict because the API never serves HTML.
"""
from __future__ import annotations

from typing import Iterable

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response


# Header set chosen to satisfy the OWASP Secure Headers Project baseline
# while remaining compatible with the JSON-API + cookie session model.
_BASE_HEADERS: dict[str, str] = {
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
    "Referrer-Policy": "no-referrer",
    "Permissions-Policy": (
        "accelerometer=(), camera=(), geolocation=(), gyroscope=(), "
        "magnetometer=(), microphone=(), payment=(), usb=()"
    ),
    # API is JSON; nothing should ever execute or embed anything.
    "Content-Security-Policy": (
        "default-src 'none'; frame-ancestors 'none'; base-uri 'none'; "
        "form-action 'none'"
    ),
    "Cross-Origin-Opener-Policy": "same-origin",
    "Cross-Origin-Resource-Policy": "same-site",
    "X-Permitted-Cross-Domain-Policies": "none",
}


class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    def __init__(self, app, *, hsts: bool, hsts_max_age: int = 63_072_000) -> None:
        super().__init__(app)
        self._hsts = hsts
        self._hsts_value = (
            f"max-age={hsts_max_age}; includeSubDomains; preload" if hsts else None
        )

    async def dispatch(self, request: Request, call_next) -> Response:
        response = await call_next(request)
        for k, v in _BASE_HEADERS.items():
            # Don't clobber a header the route explicitly set.
            response.headers.setdefault(k, v)
        if self._hsts_value is not None:
            response.headers.setdefault("Strict-Transport-Security", self._hsts_value)
        # Prevent intermediaries from caching responses that may contain
        # per-user data. Routes that want long caching can override.
        response.headers.setdefault("Cache-Control", "no-store")
        return response


def header_names() -> Iterable[str]:
    """Test helper: the set of headers this middleware will set."""
    return list(_BASE_HEADERS.keys()) + ["Strict-Transport-Security", "Cache-Control"]
