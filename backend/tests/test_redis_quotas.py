"""Tests for Redis-backed quota counters (crawl + Claude)."""
import pytest
import fakeredis.aioredis as aioredis_fake
from datetime import datetime, timezone

from app.core import redis_client as _rc
from app.core.redis_client import key as rkey


@pytest.fixture(autouse=True)
async def fake_redis(monkeypatch):
    server = aioredis_fake.FakeServer()
    r = aioredis_fake.FakeRedis(server=server, decode_responses=True)
    monkeypatch.setattr(_rc, "_client", r)
    monkeypatch.setattr(_rc, "_init_attempted", True)
    monkeypatch.setattr(_rc.settings, "REDIS_URL", "redis://fake", raising=False)
    yield r
    await r.aclose()


# ── Crawl quota ───────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_crawl_quota_incrby(fake_redis):
    """check_and_consume_quota_async increments Redis counter correctly."""
    from app.services.connectors.user_web_connector import check_and_consume_quota_async

    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    k = rkey("quota", "crawl", "user-q1", today)

    # Returns (allowed, remaining). First call should be allowed and set key to 5.
    allowed, _remaining = await check_and_consume_quota_async("user-q1", 5)
    assert allowed is True
    val = await fake_redis.get(k)
    assert val is not None
    assert int(val) == 5


@pytest.mark.asyncio
async def test_crawl_quota_over_limit(fake_redis):
    """check_and_consume_quota_async returns False when daily limit exceeded."""
    from app.services.connectors.user_web_connector import (
        check_and_consume_quota_async,
        _PER_USER_DAILY_PAGE_QUOTA,
    )

    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    k = rkey("quota", "crawl", "user-q2", today)

    # Pre-fill counter to the daily limit
    await fake_redis.set(k, str(_PER_USER_DAILY_PAGE_QUOTA))

    allowed, _remaining = await check_and_consume_quota_async("user-q2", 1)
    assert allowed is False


@pytest.mark.asyncio
async def test_crawl_quota_fallback_when_redis_none(monkeypatch):
    """Falls back to in-process quota when Redis is unavailable."""
    async def _no_redis():
        return None

    monkeypatch.setattr(_rc, "get_redis", _no_redis)

    from app.services.connectors.user_web_connector import check_and_consume_quota_async

    # Should not raise; returns allowed=True (in-process fallback still has headroom)
    allowed, _remaining = await check_and_consume_quota_async("user-q3", 1)
    assert allowed is True


# ── Claude usage quota ────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_claude_record_question_increments(fake_redis):
    """record_question increments the Redis key for the user."""
    from unittest.mock import AsyncMock, MagicMock

    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    k = rkey("quota", "claude", "user-c1", today)

    # Patch DB so record_question doesn't fail without a real DB session.
    # scalar_one_or_none must be a sync callable returning None (no existing row).
    mock_db = AsyncMock()
    exec_result = MagicMock()
    exec_result.scalar_one_or_none = MagicMock(return_value=None)
    mock_db.execute = AsyncMock(return_value=exec_result)
    mock_db.add = MagicMock()
    mock_db.commit = AsyncMock()

    from app.services.claude_usage_service import claude_usage_service

    await claude_usage_service.record_question(mock_db, "user-c1", tokens_used=100)

    val = await fake_redis.get(k)
    assert val is not None
    assert int(val) >= 1


@pytest.mark.asyncio
async def test_claude_get_usage_redis_fast_path(fake_redis):
    """get_usage reads from Redis fast path when key exists."""
    from unittest.mock import AsyncMock

    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    k = rkey("quota", "claude", "user-c2", today)
    await fake_redis.set(k, "7")

    mock_db = AsyncMock()
    from app.services.claude_usage_service import claude_usage_service

    result = await claude_usage_service.get_usage(mock_db, "user-c2")
    assert result["question_count"] == 7
    # DB should NOT be called when Redis fast path succeeds
    mock_db.execute.assert_not_called()


# ── Budget cache ──────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_budget_cache_set_and_get(fake_redis):
    """set_usage_cache and get_usage_cached round-trip."""
    from app.core.budget_cache import set_usage_cache, get_usage_cached

    await set_usage_cache(
        "user-b1",
        daily_tokens=1000,
        daily_cost=0.05,
        monthly_tokens=25000,
        monthly_cost=1.25,
    )
    result = await get_usage_cached("user-b1")
    assert result is not None
    assert result["daily_tokens"] == 1000
    assert abs(result["daily_cost"] - 0.05) < 0.001
    assert result["monthly_tokens"] == 25000


@pytest.mark.asyncio
async def test_budget_cache_increment(fake_redis):
    """increment_usage_cache should add to existing counters."""
    from app.core.budget_cache import set_usage_cache, increment_usage_cache, get_usage_cached

    await set_usage_cache(
        "user-b2",
        daily_tokens=500,
        daily_cost=0.01,
        monthly_tokens=5000,
        monthly_cost=0.10,
    )
    await increment_usage_cache("user-b2", tokens=100, cost=0.002)

    result = await get_usage_cached("user-b2")
    assert result is not None
    assert result["daily_tokens"] == 600
    assert abs(result["monthly_tokens"] - 5100) < 1


@pytest.mark.asyncio
async def test_budget_cache_invalidate(fake_redis):
    """invalidate_usage_cache removes all keys for a user."""
    from app.core.budget_cache import set_usage_cache, invalidate_usage_cache, get_usage_cached

    await set_usage_cache(
        "user-b3",
        daily_tokens=100,
        daily_cost=0.01,
        monthly_tokens=1000,
        monthly_cost=0.10,
    )
    await invalidate_usage_cache("user-b3")
    result = await get_usage_cached("user-b3")
    assert result is None
