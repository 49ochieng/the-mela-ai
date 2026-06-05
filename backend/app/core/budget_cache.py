"""Redis-backed budget/usage cache for Mela AI.

Provides fast read/write for per-user token and cost usage within a budget
period, avoiding repeated SUM() aggregate queries on the ``model_usage`` table
for every chat request.

Key schema:
    ``{PREFIX}budget:{user_id}:daily:{YYYY-MM-DD}``  → Redis Hash
        {"tokens": "<int>", "cost": "<float>"}
    ``{PREFIX}budget:{user_id}:monthly:{YYYY-MM}``   → Redis Hash
        {"tokens": "<int>", "cost": "<float>"}

TTL policy:
    Daily key: 2 × 86 400 s (48 hours).
    Monthly key: 2 × 31 days (62 days).

The DB remains the authoritative source.  This cache is a fast-read
write-through projection:
  1. ``check_budget`` in budget_service reads the cache first.
  2. On a cache miss it queries the DB and writes the result to cache
     (write-on-read).
  3. After every ``ModelUsage`` row is committed, ``increment_usage_cache``
     is called to keep the cache in sync.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Optional

from app.core.redis_client import get_redis, key as rkey

logger = logging.getLogger(__name__)

_DAILY_TTL = 2 * 86_400       # 48 hours
_MONTHLY_TTL = 2 * 31 * 86_400  # ~62 days


def _daily_key(user_id: str) -> str:
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    return rkey("budget", user_id, "daily", today)


def _monthly_key(user_id: str) -> str:
    month = datetime.now(timezone.utc).strftime("%Y-%m")
    return rkey("budget", user_id, "monthly", month)


async def get_usage_cached(user_id: str) -> Optional[dict]:
    """Return cached usage totals for the current day and month, or ``None`` on miss.

    Returns:
        {
          "daily_tokens": int,
          "daily_cost": float,
          "monthly_tokens": int,
          "monthly_cost": float,
        }
    """
    r = await get_redis()
    if r is None:
        return None
    try:
        async with r.pipeline(transaction=False) as pipe:
            pipe.hgetall(_daily_key(user_id))
            pipe.hgetall(_monthly_key(user_id))
            daily_raw, monthly_raw = await pipe.execute()

        if not daily_raw and not monthly_raw:
            return None  # Full cache miss — caller must query DB.

        return {
            "daily_tokens": int(daily_raw.get("tokens", 0) or 0),
            "daily_cost": float(daily_raw.get("cost", 0.0) or 0.0),
            "monthly_tokens": int(monthly_raw.get("tokens", 0) or 0),
            "monthly_cost": float(monthly_raw.get("cost", 0.0) or 0.0),
        }
    except Exception as exc:
        logger.warning("budget_cache get error (%s); falling back to DB", exc)
        return None


async def set_usage_cache(
    user_id: str,
    *,
    daily_tokens: int,
    daily_cost: float,
    monthly_tokens: int,
    monthly_cost: float,
) -> None:
    """Write current period usage totals to cache (write-on-read path).

    Called by ``budget_service.check_budget`` after a DB aggregate query so
    subsequent requests hit Redis instead of the DB.
    """
    r = await get_redis()
    if r is None:
        return
    try:
        async with r.pipeline(transaction=True) as pipe:
            dk = _daily_key(user_id)
            mk = _monthly_key(user_id)
            pipe.hset(dk, mapping={"tokens": daily_tokens, "cost": str(daily_cost)})
            pipe.expire(dk, _DAILY_TTL)
            pipe.hset(mk, mapping={"tokens": monthly_tokens, "cost": str(monthly_cost)})
            pipe.expire(mk, _MONTHLY_TTL)
            await pipe.execute()
    except Exception as exc:
        logger.warning("budget_cache set error (%s)", exc)


async def increment_usage_cache(user_id: str, tokens: int, cost: float) -> None:
    """Atomically increment usage counters after a successful ModelUsage write.

    Uses ``HINCRBYFLOAT`` so fractional costs are safe.  The key TTL is
    refreshed after the increment.  If the key does not exist yet (Redis
    restart, first request of the day) the HINCRBYFLOAT will create it, but
    it will have no TTL — the next call to ``set_usage_cache`` will set the
    correct TTL on the next DB-fallback read.
    """
    r = await get_redis()
    if r is None:
        return
    try:
        async with r.pipeline(transaction=True) as pipe:
            for k, ttl in ((_daily_key(user_id), _DAILY_TTL), (_monthly_key(user_id), _MONTHLY_TTL)):
                pipe.hincrbyfloat(k, "tokens", tokens)
                pipe.hincrbyfloat(k, "cost", cost)
                pipe.expire(k, ttl)
            await pipe.execute()
    except Exception as exc:
        logger.warning("budget_cache increment error (%s)", exc)


async def invalidate_usage_cache(user_id: str) -> None:
    """Delete all cached usage keys for a user (e.g. after budget reset)."""
    r = await get_redis()
    if r is None:
        return
    try:
        await r.delete(_daily_key(user_id), _monthly_key(user_id))
    except Exception as exc:
        logger.warning("budget_cache invalidate error (%s)", exc)
