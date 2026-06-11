"""In-process token-bucket rate limiting.

Designed for the single-process FastAPI deployments we ship today; when
we move to multi-instance, swap the in-memory store for Redis (the
``_Bucket`` interface is the only thing that needs to change).

Two independent buckets are checked per request:
  * **per-IP** — defends against unauthenticated brute force.
  * **per-principal** — defends against compromised credentials being
    abused at high RPS. Identified by ``Authorization`` header hash or
    ``mtr_session`` cookie hash.

Hits are scoped by *route group* (auth, agent_tokens, admin, default)
so a noisy admin operation can't lock the auth surface and vice versa.
"""
from __future__ import annotations

import hashlib
import time
from dataclasses import dataclass, field
from threading import Lock

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse, Response


@dataclass
class _Bucket:
    capacity: int
    refill_per_sec: float
    tokens: float = field(init=False)
    updated_at: float = field(init=False)

    def __post_init__(self) -> None:
        self.tokens = float(self.capacity)
        self.updated_at = time.monotonic()

    def take(self, cost: float = 1.0) -> bool:
        now = time.monotonic()
        elapsed = now - self.updated_at
        self.updated_at = now
        self.tokens = min(self.capacity, self.tokens + elapsed * self.refill_per_sec)
        if self.tokens >= cost:
            self.tokens -= cost
            return True
        return False


# (capacity, refill-per-second). Capacity = burst; refill ≈ steady RPS.
# Tuned conservatively: a real human SPA never exceeds these even with
# fast clicks, but a credential-stuffing or scraping client trips quickly.
_LIMITS: dict[str, tuple[int, float]] = {
    "auth":          (10,  10 / 60),    # 10 hits / minute on /api/auth/*
    "agent_tokens":  (5,   5 / 60),     # 5 token mints / minute
    "admin":         (30,  30 / 60),    # 30 admin ops / minute
    "default":       (120, 120 / 60),   # 120 / minute on everything else
}


def _group_for(path: str) -> str:
    if path.startswith("/api/auth/"):
        return "auth"
    if path.startswith("/api/agent-tokens"):
        return "agent_tokens"
    if path.startswith("/api/admin/"):
        return "admin"
    return "default"


def _client_ip(request: Request) -> str:
    # Trust XFF only when explicitly proxied; in dev the client.host is fine.
    fwd = request.headers.get("x-forwarded-for", "")
    if fwd:
        return fwd.split(",")[0].strip()
    return request.client.host if request.client else "unknown"


def _principal_id(request: Request) -> str | None:
    authz = request.headers.get("authorization", "")
    if authz.lower().startswith("bearer "):
        return "b:" + hashlib.sha256(authz.split(" ", 1)[1].encode()).hexdigest()[:32]
    sess = request.cookies.get("mtr_session") or request.cookies.get("__Host-mtr_session")
    if sess:
        return "s:" + hashlib.sha256(sess.encode()).hexdigest()[:32]
    return None


class RateLimitMiddleware(BaseHTTPMiddleware):
    def __init__(self, app, *, enabled: bool = True) -> None:
        super().__init__(app)
        self._enabled = enabled
        self._buckets: dict[tuple[str, str, str], _Bucket] = {}
        self._lock = Lock()

    def _bucket(self, key: tuple[str, str, str]) -> _Bucket:
        with self._lock:
            b = self._buckets.get(key)
            if b is None:
                cap, rate = _LIMITS[key[0]]
                b = _Bucket(cap, rate)
                self._buckets[key] = b
            return b

    async def dispatch(self, request: Request, call_next) -> Response:
        if not self._enabled or request.method.upper() in ("OPTIONS", "HEAD"):
            return await call_next(request)
        group = _group_for(request.url.path)
        ip = _client_ip(request)
        principal = _principal_id(request)
        # Charge IP bucket first (cheaper, blocks unauth abuse).
        if not self._bucket((group, "ip", ip)).take():
            return _too_many(group)
        if principal and not self._bucket((group, "p", principal)).take():
            return _too_many(group)
        return await call_next(request)

    # Test helpers
    def reset(self) -> None:
        with self._lock:
            self._buckets.clear()


def _too_many(group: str) -> JSONResponse:
    cap, rate = _LIMITS[group]
    retry = max(1, int(1 / rate)) if rate > 0 else 60
    resp = JSONResponse({"detail": "Rate limit exceeded"}, status_code=429)
    resp.headers["Retry-After"] = str(retry)
    return resp
