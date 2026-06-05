"""
Mela AI - Custom Middleware
"""

import logging
import time
from collections import defaultdict, deque
from typing import Callable, Dict
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response, JSONResponse

from app.core.config import settings

logger = logging.getLogger(__name__)

# Paths that should never be logged or rate-limited (noise reduction).
# Worker-callback prefixes also exempt — they authenticate via
# X-Worker-Id/X-Worker-Api-Key, not human JWTs, so the per-IP / per-Auth
# rate-limit bucket is the wrong gate for them.
_SILENT_PATHS = frozenset(["/health", "/", "/docs", "/redoc", "/openapi.json"])
# Phase 6A: external MCP clients authenticate via X-Api-Key, not human
# JWTs, so the /mcp/ prefix joins ingest in skipping the rate-limit
# middleware.
#
# SSE streaming endpoints (text/event-stream) MUST bypass BaseHTTPMiddleware —
# Starlette's BaseHTTPMiddleware buffers responses to inspect them and
# converts streaming-generator exceptions into "RuntimeError: No response
# returned." which crashes the connection.  Listing these prefixes here
# routes them around both RequestLoggingMiddleware AND RateLimitMiddleware.
_SILENT_PATH_PREFIXES = (
    "/api/v1/ingest/",
    "/mcp/",
    "/api/v1/orchestration/events/stream",
    "/api/v1/chat/completions/stream",
)


def _is_silent_path(path: str) -> bool:
    return path in _SILENT_PATHS or any(
        path.startswith(p) for p in _SILENT_PATH_PREFIXES
    )


# Path prefixes that need pure-ASGI passthrough (no BaseHTTPMiddleware
# wrapping).  BaseHTTPMiddleware buffers streaming responses and converts
# any exception/cancel during the stream into RuntimeError("No response
# returned.") — fatal for SSE.  These paths are routed around the
# BaseHTTPMiddleware task-group entirely via overridden __call__.
_STREAMING_BYPASS_PREFIXES = (
    "/api/v1/orchestration/events/stream",
    "/api/v1/chat/completions/stream",
)


def _is_streaming_path(scope) -> bool:
    if scope.get("type") != "http":
        return False
    path = scope.get("path", "")
    return any(path.startswith(p) for p in _STREAMING_BYPASS_PREFIXES)


class RequestLoggingMiddleware(BaseHTTPMiddleware):
    """Log requests at WARNING+ only — skip health-checks and CORS preflights."""

    async def __call__(self, scope, receive, send):
        # Streaming endpoints MUST skip BaseHTTPMiddleware's task-group
        # wrapping or SSE responses crash with "No response returned."
        if _is_streaming_path(scope):
            await self.app(scope, receive, send)
            return
        await super().__call__(scope, receive, send)

    async def dispatch(self, request: Request, call_next: Callable) -> Response:
        # Skip noisy paths and OPTIONS preflights entirely
        if request.method == "OPTIONS" or _is_silent_path(request.url.path):
            return await call_next(request)

        start_time = time.time()
        try:
            response = await call_next(request)
            elapsed = time.time() - start_time
            level = logging.WARNING if response.status_code >= 500 else (
                logging.INFO if elapsed > 0.5 or response.status_code >= 400
                else logging.DEBUG
            )
            logger.log(
                level,
                "%s %s -> %d  %.3fs",
                request.method, request.url.path, response.status_code, elapsed,
            )

            # Alert on 5xx responses that weren't caught by the global handler
            # (e.g. streaming errors, middleware-layer failures).
            if response.status_code >= 500:
                try:
                    import asyncio as _asyncio
                    from app.services.alert_service import send_alert, AlertIncident
                    incident = AlertIncident(
                        title=f"HTTP {response.status_code} on {request.url.path}",
                        severity="critical",
                        code="HTTP_5XX",
                        route=f"{request.method} {request.url.path}",
                        tenant_id=getattr(request.state, "tenant_id", None),
                        error_message=(
                            f"{request.method} {request.url.path} returned "
                            f"{response.status_code} in {elapsed:.2f}s"
                        ),
                    )
                    _asyncio.create_task(send_alert(incident))
                except Exception:
                    pass

            return response
        except Exception as e:
            elapsed = time.time() - start_time
            logger.error("Unhandled error %s %s — %s  %.3fs",
                         request.method, request.url.path, e, elapsed)
            raise


class EmbedFrameMiddleware(BaseHTTPMiddleware):
    """Phase 6B: control which origins may iframe Mela's embed routes.

    Default: ``X-Frame-Options: SAMEORIGIN`` on every ``/embed`` and
    ``/api/v1/embed`` response — Mela cannot be framed by third-party
    sites unless explicitly allowed.

    When ``MELA_EMBED_ALLOWED_ORIGINS`` lists at least one origin and
    the request's ``Origin`` header matches, we drop ``X-Frame-Options``
    in favour of ``Content-Security-Policy: frame-ancestors`` (the
    modern equivalent that supports a list of origins) and add the
    matching CORS headers so the embedding app's preflight succeeds.
    """

    _EMBED_PREFIXES = ("/embed", "/api/v1/embed")

    async def dispatch(self, request: Request, call_next: Callable) -> Response:
        response = await call_next(request)
        path = request.url.path
        if not any(path.startswith(p) for p in self._EMBED_PREFIXES):
            return response

        allowed = settings.embed_allowed_origin_list
        origin = request.headers.get("origin", "")
        if allowed and origin in allowed:
            # Modern frame policy — supports multiple origins, unlike
            # the legacy X-Frame-Options header.
            response.headers["Content-Security-Policy"] = (
                "frame-ancestors " + " ".join(allowed)
            )
            # Starlette's MutableHeaders supports __delitem__ but not pop.
            if "X-Frame-Options" in response.headers:
                del response.headers["X-Frame-Options"]
            response.headers["Access-Control-Allow-Origin"] = origin
            response.headers["Vary"] = "Origin"
        else:
            response.headers["X-Frame-Options"] = "SAMEORIGIN"
            if allowed:
                # Allow-list configured but request didn't match — set
                # the modern CSP equivalent so admins can audit which
                # origins are configured.
                response.headers["Content-Security-Policy"] = (
                    "frame-ancestors 'self' " + " ".join(allowed)
                )
        return response


class RateLimitMiddleware(BaseHTTPMiddleware):
    """Sliding-window rate limiter.

    Fast path (when Redis is available): uses Redis INCR + EXPIRE so limits
    are enforced consistently across all replicas.  The Redis key is:
        ``{PREFIX}ratelimit:{client_id_hash}:{epoch_window}``
    where ``epoch_window = int(unix_ts // window_seconds)`` — a new bucket
    opens every ``window_seconds`` seconds.

    Fallback (when Redis is unavailable): the original in-process deque
    behaviour is used, which is safe for single-replica deployments.
    """

    def __init__(self, app, requests_limit: int = None, window_seconds: int = None):
        super().__init__(app)
        self.requests_limit = requests_limit or settings.RATE_LIMIT_REQUESTS
        self.window_seconds = window_seconds or settings.RATE_LIMIT_WINDOW
        # In-process fallback: deque per client, timestamps, oldest at left.
        # Separate buckets per route so a per-route limit doesn't poison the
        # global limit (and vice versa).
        self.requests: Dict[str, deque] = defaultdict(deque)

    async def __call__(self, scope, receive, send):
        # Same SSE bypass — see RequestLoggingMiddleware.__call__.
        if _is_streaming_path(scope):
            await self.app(scope, receive, send)
            return
        await super().__call__(scope, receive, send)

    def _route_override(self, path: str) -> tuple[int, str] | None:
        """Per-route limit override. Returns (limit_per_min, bucket_label) or None.

        Reads settings at request time so config changes take effect without
        restarting the app. A 0 / unset value means "use the global limit".
        """
        if path.startswith("/api/v1/chat/completions"):
            limit = getattr(settings, "CHAT_RATE_LIMIT_PER_MIN", 0) or 0
            if limit > 0:
                return limit, "chat"
        if path.startswith("/api/v1/admin/me"):
            limit = getattr(settings, "ADMIN_ME_RATE_LIMIT_PER_MIN", 0) or 0
            if limit > 0:
                return limit, "adminme"
        return None

    async def dispatch(self, request: Request, call_next: Callable) -> Response:
        if request.method == "OPTIONS" or _is_silent_path(request.url.path):
            return await call_next(request)

        client_id = self._get_client_id(request)
        current_time = time.time()

        # Per-route override picks its own (limit, window=60s, bucket_label) tuple
        # so the chat bucket and the global bucket never collide.
        override = self._route_override(request.url.path)
        if override is not None:
            requests_limit, bucket_label = override
            window_seconds = 60
        else:
            requests_limit = self.requests_limit
            window_seconds = self.window_seconds
            bucket_label = "global"

        # ── Redis fast path ──────────────────────────────────────────────────
        try:
            from app.core.redis_client import get_redis, key as rkey

            r = await get_redis()
            if r is not None:
                epoch_window = int(current_time // window_seconds)
                rk = rkey("ratelimit", bucket_label, client_id, str(epoch_window))
                count = await r.incr(rk)
                if count == 1:
                    # First hit in this window — set TTL so key auto-expires.
                    await r.expire(rk, window_seconds * 2)
                if count > requests_limit:
                    logger.warning(
                        "Rate limit exceeded (Redis, bucket=%s): %s",
                        bucket_label, client_id,
                    )
                    return JSONResponse(
                        status_code=429,
                        content={
                            "error": "Rate limit exceeded",
                            "message": (
                                f"Too many requests. Please try again in "
                                f"{window_seconds} seconds."
                            ),
                            "retry_after": window_seconds,
                            "bucket": bucket_label,
                        },
                    )
                return await call_next(request)
        except Exception as exc:
            logger.debug("Rate-limit Redis error (%s); falling back to in-process", exc)

        # ── In-process fallback (deque) ──────────────────────────────────────
        window_start = current_time - window_seconds
        bucket_key = f"{bucket_label}:{client_id}"
        bucket = self.requests[bucket_key]
        # O(1) cleanup: pop expired timestamps from the left.
        while bucket and bucket[0] < window_start:
            bucket.popleft()

        if len(bucket) >= requests_limit:
            logger.warning(
                "Rate limit exceeded (bucket=%s): %s",
                bucket_label, client_id,
            )
            return JSONResponse(
                status_code=429,
                content={
                    "error": "Rate limit exceeded",
                    "message": f"Too many requests. Please try again in {window_seconds} seconds.",
                    "retry_after": window_seconds,
                    "bucket": bucket_label,
                },
            )

        bucket.append(current_time)
        return await call_next(request)

    def _get_client_id(self, request: Request) -> str:
        auth = request.headers.get("Authorization", "")
        if auth:
            # Hash auth token so the raw bearer token never touches Redis keys.
            import hashlib
            return "auth:" + hashlib.sha256(auth.encode()).hexdigest()[:16]
        return f"ip:{request.client.host if request.client else 'unknown'}"
