"""Tests for Redis-backed memory caching in memory_service.py."""
import pytest
import fakeredis.aioredis as aioredis_fake
import json

from app.core import redis_client as _rc
from app.core.redis_client import key as rkey


@pytest.fixture(autouse=True)
async def fake_redis(monkeypatch):
    server = aioredis_fake.FakeServer()
    r = aioredis_fake.FakeRedis(server=server, decode_responses=True)
    monkeypatch.setattr(_rc, "_client", r)
    monkeypatch.setattr(_rc, "_init_attempted", True)
    # get_redis() bails early when REDIS_URL is unset (CI default), so patch
    # it to a truthy value so functions routed through get_redis() see the fake.
    monkeypatch.setattr(_rc.settings, "REDIS_URL", "redis://fake", raising=False)
    yield r
    await r.aclose()


# ── Long-term memory cache ────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_ltm_key_written_to_redis(fake_redis):
    """_ltm_key should produce the expected Redis key format."""
    k = rkey("ltmem", "user123", "personal", "none")
    await fake_redis.set(k, json.dumps([{"id": "m1", "content": "hello"}]))
    raw = await fake_redis.get(k)
    assert raw is not None
    parsed = json.loads(raw)
    assert parsed[0]["content"] == "hello"


@pytest.mark.asyncio
async def test_smem_key_written_to_redis(fake_redis):
    """Session memory key should store and retrieve JSON correctly."""
    k = rkey("smem", "conv-abc")
    payload = {
        "id": "sm1",
        "conversation_id": "conv-abc",
        "summary": "Test summary",
        "message_count": 5,
        "last_updated": "2024-01-01T00:00:00",
    }
    await fake_redis.set(k, json.dumps(payload), ex=2592000)
    raw = await fake_redis.get(k)
    assert raw is not None
    parsed = json.loads(raw)
    assert parsed["summary"] == "Test summary"


@pytest.mark.asyncio
async def test_ltm_cache_miss_returns_none(fake_redis):
    """A missing LTM key should return None (cache miss)."""
    raw = await fake_redis.get(rkey("ltmem", "ghost", "personal", "none"))
    assert raw is None


@pytest.mark.asyncio
async def test_smem_invalidate(fake_redis):
    """Deleting an smem key should yield a cache miss."""
    k = rkey("smem", "conv-del")
    await fake_redis.set(k, json.dumps({"summary": "old"}))
    await fake_redis.delete(k)
    raw = await fake_redis.get(k)
    assert raw is None


# ── Context cache ─────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_context_cache_append_and_get():
    """append_message_to_context + get_active_context round-trip."""
    from app.core.context_cache import append_message_to_context, get_active_context, invalidate_context

    conv_id = "conv-ctx-test"
    msg = {"role": "user", "content": "hi"}

    await append_message_to_context(conv_id, msg)
    result = await get_active_context(conv_id)
    assert len(result) == 1
    assert result[0]["content"] == "hi"

    await invalidate_context(conv_id)
    result2 = await get_active_context(conv_id)
    assert result2 == []


@pytest.mark.asyncio
async def test_context_cache_fallback_when_redis_none(monkeypatch):
    """When Redis is None, context falls back to the provided dict."""
    async def _no_redis():
        return None

    monkeypatch.setattr(_rc, "get_redis", _no_redis)

    from app.core.context_cache import append_message_to_context, get_active_context

    fallback = {}
    conv_id = "conv-fallback"
    await append_message_to_context(conv_id, {"role": "user", "content": "test"}, _fallback=fallback)
    msgs = await get_active_context(conv_id, _fallback=fallback)
    assert len(msgs) == 1
    assert msgs[0]["content"] == "test"
