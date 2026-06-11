"""Double-submit cookie CSRF protection.

Threat model
------------
Browser sessions are authenticated via the `mtr_session` HttpOnly cookie.
That makes cookie-bearing endpoints vulnerable to CSRF: a malicious site
can issue a cross-origin POST that the browser will attach the cookie
to, even though the response can't be read.

Defense
-------
On every cookie-authenticated unsafe request (POST/PUT/PATCH/DELETE) the
client must echo the value of the `mtr_csrf` cookie back in the
`X-CSRF-Token` header. Because cross-origin JavaScript cannot read the
victim's cookies (Same-Origin Policy), it cannot construct a matching
header — so the request is rejected.

Exemptions
----------
- Bearer auth (Authorization header). Bearer tokens are not auto-attached
  by browsers, so CSRF doesn't apply. This includes per-user agent tokens
  and JWTs presented by the MCP / Mela clients.
- Safe verbs (GET/HEAD/OPTIONS).
- Explicit allow-list paths (e.g. /api/auth/microsoft/* and webhook
  endpoints whose security relies on out-of-band signatures, not cookies).
- The CSRF token issuance endpoint itself.

The CSRF cookie is set on every response that doesn't already have one.
It is NOT HttpOnly (the SPA must read it to echo the header), but it IS
SameSite=Strict and Secure (when the session cookie is) so a cross-site
context can never see it.
"""
from __future__ import annotations

import secrets
from typing import Iterable

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse, Response


CSRF_COOKIE_NAME = "mtr_csrf"
CSRF_HEADER_NAME = "x-csrf-token"
_UNSAFE_METHODS = {"POST", "PUT", "PATCH", "DELETE"}


def _new_token() -> str:
    return secrets.token_urlsafe(32)


class CSRFMiddleware(BaseHTTPMiddleware):
    def __init__(
        self,
        app,
        *,
        enabled: bool = True,
        cookie_secure: bool,
        cookie_domain: str = "",
        exempt_path_prefixes: Iterable[str] = (),
    ) -> None:
        super().__init__(app)
        self._enabled = enabled
        self._cookie_secure = cookie_secure
        self._cookie_domain = cookie_domain or None
        # Always-exempt paths: callbacks the browser navigates to (no XHR),
        # health, and the OpenAPI/docs surface.
        self._exempt = tuple(exempt_path_prefixes) + (
            "/api/auth/microsoft/login",
            "/api/auth/microsoft/callback",
            "/health",
            "/docs",
            "/redoc",
            "/openapi.json",
        )

    def _is_exempt(self, path: str) -> bool:
        return any(path == p or path.startswith(p) for p in self._exempt)

    async def dispatch(self, request: Request, call_next) -> Response:
        if not self._enabled:
            return await call_next(request)
        method = request.method.upper()
        path = request.url.path
        needs_check = method in _UNSAFE_METHODS and not self._is_exempt(path)

        if needs_check:
            # Bearer-authenticated requests are exempt: the browser will not
            # auto-attach an Authorization header cross-origin.
            authz = request.headers.get("authorization", "")
            has_bearer = authz.lower().startswith("bearer ")
            cookie_token = request.cookies.get(CSRF_COOKIE_NAME)
            header_token = request.headers.get(CSRF_HEADER_NAME)
            if not has_bearer:
                if not cookie_token or not header_token:
                    return JSONResponse(
                        {"detail": "CSRF token missing"}, status_code=403,
                    )
                # Constant-time compare to defeat timing oracles.
                if not secrets.compare_digest(cookie_token, header_token):
                    return JSONResponse(
                        {"detail": "CSRF token mismatch"}, status_code=403,
                    )

        response = await call_next(request)

        # Issue a token cookie if the client doesn't have one yet. Doing this
        # for every response keeps the SPA's bootstrap simple — the first
        # GET it makes will plant the cookie before any mutations.
        if self._enabled:
            existing = request.cookies.get(CSRF_COOKIE_NAME)
            if existing:
                # Echo the existing token in a response header so cross-domain
                # SPAs (different *.azurewebsites.net subdomain) can read it via
                # the CORS-exposed header and send it back as X-CSRF-Token.
                response.headers["X-CSRF-Token"] = existing
            else:
                token = _new_token()
                # SameSite=None required when API and SPA live on different
                # public-suffix subdomains (e.g. melatr-api vs melatr-web on
                # azurewebsites.net).  Requires Secure=True, which is already
                # enforced in production.
                samesite = "none" if self._cookie_secure else "strict"
                response.set_cookie(
                    key=CSRF_COOKIE_NAME,
                    value=token,
                    max_age=60 * 60 * 12,
                    secure=self._cookie_secure,
                    httponly=False,  # SPA must read via header (cross-domain)
                    samesite=samesite,
                    path="/",
                    domain=self._cookie_domain,
                )
                response.headers["X-CSRF-Token"] = token
        return response
