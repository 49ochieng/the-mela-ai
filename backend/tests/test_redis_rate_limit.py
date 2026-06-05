"""Tests for Redis-backed distributed rate limiting in middleware.py."""
import pytest
import fakeredis.aioredis as aioredis_fake
from unittest.mock import MagicMock, AsyncMock

from app.core import redis_client as _rc


@pytest.fixture(autouse=True)
async def fake_redis(monkeypatch):
    server = aioredis_fake.FakeServer()
    r = aioredis_fake.FakeRedis(server=server, decode_responses=True)
    monkeypatch.setattr(_rc, "_client", r)
    monkeypatch.setattr(_rc, "_init_attempted", True)
    monkeypatch.setattr(_rc.settings, "REDIS_URL", "redis://fake", raising=False)
    yield r
    await r.aclose()


@pytest.mark.asyncio
async def test_redis_incr_increments_counter(fake_redis):
    """Calling INCR on the same key should accumulate counts."""
    from app.core.redis_client import key as rkey
    import time

    window = int(time.time() // 60)
    k = rkey("ratelimit", "testhash", str(window))

    val1 = await fake_redis.incr(k)
    val2 = await fake_redis.incr(k)
    val3 = await fake_redis.incr(k)

    assert val1 == 1
    assert val2 == 2
    assert val3 == 3


# NB: we exercise the middleware via dispatch() directly rather than through
# starlette.testclient.TestClient. Newer httpx (>=0.28) removed the `app=`
# shortcut TestClient relies on, so TestClient(app) raises TypeError in this
# environment. Calling dispatch() with a hand-built Request is version-proof
# and a more precise test of the Redis code path.

def _make_request(path: str = "/api/v1/chat", auth: str = "Bearer t"):
    from starlette.requests import Request
    headers = []
    if auth:
        headers.append((b"authorization", auth.encode()))
    scope = {
        "type": "http",
        "method": "GET",
        "path": path,
        "headers": headers,
        "client": ("127.0.0.1", 12345),
        "query_string": b"",
    }
    return Request(scope)


@pytest.mark.asyncio
async def test_rate_limit_middleware_redis_path(monkeypatch, fake_redis):
    """RateLimitMiddleware uses Redis INCR and allows requests under limit."""
    from app.core.middleware import RateLimitMiddleware
    from starlette.responses import PlainTextResponse

    mw = RateLimitMiddleware(app=None, requests_limit=100, window_seconds=60)

    async def call_next(_request):
        return PlainTextResponse("ok")

    resp = await mw.dispatch(_make_request(auth="Bearer testtoken"), call_next)
    assert resp.status_code == 200
    # Confirm the Redis counter was incremented (the fast path actually ran).
    import time
    from app.core.redis_client import key as rkey
    window = int(time.time() // 60)
    keys = await fake_redis.keys(rkey("ratelimit", "global", "*", str(window)))
    assert len(keys) >= 1


@pytest.mark.asyncio
async def test_rate_limit_fallback_on_redis_error(monkeypatch):
    """When Redis is unavailable, falls back to in-process deque."""
    async def _no_redis():
        return None

    monkeypatch.setattr(_rc, "get_redis", _no_redis)

    from app.core.middleware import RateLimitMiddleware
    from starlette.responses import PlainTextResponse

    mw = RateLimitMiddleware(app=None, requests_limit=100, window_seconds=60)

    async def call_next(_request):
        return PlainTextResponse("ok")

    resp = await mw.dispatch(_make_request(auth="Bearer fallbacktoken"), call_next)
    assert resp.status_code == 200
