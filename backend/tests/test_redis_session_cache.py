"""Tests for session_cache.py Redis fast path using fakeredis."""
import pytest
import fakeredis.aioredis as aioredis_fake

from app.core import session_cache as sc
from app.core import redis_client as _rc


@pytest.fixture(autouse=True)
async def fake_redis(monkeypatch):
    """Patch get_redis() to return a fakeredis instance."""
    server = aioredis_fake.FakeServer()
    r = aioredis_fake.FakeRedis(server=server, decode_responses=True)
    monkeypatch.setattr(_rc, "_client", r)
    monkeypatch.setattr(_rc, "_init_attempted", True)
    monkeypatch.setattr(_rc.settings, "REDIS_URL", "redis://fake", raising=False)
    yield r
    await r.aclose()


@pytest.mark.asyncio
async def test_set_and_get_session():
    data = {
        "user_id": "u1",
        "tenant_id": "t1",
        "expires_at": "2099-01-01T00:00:00",
        "revoked_at": None,
    }
    await sc.set_session_cache("jti-abc", data)
    result = await sc.get_session_from_cache("jti-abc")
    assert result is not None
    assert result["user_id"] == "u1"
    assert result["tenant_id"] == "t1"


@pytest.mark.asyncio
async def test_get_session_cache_miss():
    result = await sc.get_session_from_cache("nonexistent-jti")
    assert result is None


@pytest.mark.asyncio
async def test_invalidate_session():
    data = {
        "user_id": "u2",
        "tenant_id": "t2",
        "expires_at": "2099-01-01T00:00:00",
        "revoked_at": None,
    }
    await sc.set_session_cache("jti-xyz", data)
    await sc.invalidate_session_cache("jti-xyz")
    result = await sc.get_session_from_cache("jti-xyz")
    assert result is None


@pytest.mark.asyncio
async def test_touch_session_keeps_ttl():
    data = {
        "user_id": "u3",
        "tenant_id": "t3",
        "expires_at": "2099-01-01T00:00:00",
        "revoked_at": None,
    }
    await sc.set_session_cache("jti-touch", data)
    ok = await sc.touch_session_cache("jti-touch")
    assert ok is True
    # Session should still be accessible after touch
    result = await sc.get_session_from_cache("jti-touch")
    assert result is not None


@pytest.mark.asyncio
async def test_malformed_cache_entry_treated_as_miss(fake_redis):
    """Malformed / missing required fields → treated as cache miss."""
    from app.core.redis_client import key as rkey
    k = rkey("session", "jti-bad")
    await fake_redis.set(k, "not-json")
    result = await sc.get_session_from_cache("jti-bad")
    assert result is None


@pytest.mark.asyncio
async def test_invalidate_all_user_sessions():
    jtis = ["jti-1", "jti-2", "jti-3"]
    data = {
        "user_id": "u4",
        "tenant_id": None,
        "expires_at": "2099-01-01T00:00:00",
        "revoked_at": None,
    }
    for jti in jtis:
        await sc.set_session_cache(jti, data)
    await sc.invalidate_all_user_sessions_cache(jtis)
    for jti in jtis:
        result = await sc.get_session_from_cache(jti)
        assert result is None, f"{jti} should be invalidated"
