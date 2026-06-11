"""Request-context propagation for structured logging + audit.

Sets a per-request UUID and exposes the calling IP / user agent through a
``contextvars.ContextVar``. The audit subsystem reads these so that every
audit row carries the full forensic context, and the JSON log formatter
emits ``request_id`` on every log line.
"""
from __future__ import annotations

import contextvars
import uuid
from dataclasses import dataclass

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response


@dataclass
class RequestContextInfo:
    request_id: str
    ip: str | None
    user_agent: str | None


_ctx: contextvars.ContextVar[RequestContextInfo | None] = contextvars.ContextVar(
    "mtr_request_ctx", default=None,
)


def current() -> RequestContextInfo | None:
    return _ctx.get()


class RequestContextMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next) -> Response:
        # Honour an inbound X-Request-ID from a trusted reverse proxy, but
        # constrain length to avoid log-injection through giant ids.
        rid = request.headers.get("x-request-id", "")[:36] or uuid.uuid4().hex
        ip = request.headers.get("x-forwarded-for", "").split(",")[0].strip()
        if not ip:
            ip = request.client.host if request.client else None
        ua = request.headers.get("user-agent", "")[:255] or None
        token = _ctx.set(RequestContextInfo(request_id=rid, ip=ip or None, user_agent=ua))
        try:
            response = await call_next(request)
        finally:
            _ctx.reset(token)
        response.headers["X-Request-ID"] = rid
        return response
