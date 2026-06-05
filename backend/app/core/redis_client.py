"""Singleton async Redis client (feature-flagged, production-hardened).

Returns ``None`` when ``REDIS_URL`` is unset so call sites can fall back to
their existing in-process behaviour with a single ``if r is None`` check.

Usage:
    from app.core.redis_client import get_redis, key as rkey
    r = await get_redis()
    if r is not None:
        await r.set(rkey("ns", "id"), "v", ex=60)

The dependency is optional: when the ``redis`` package is not installed,
this module logs once at WARNING and continues to return ``None``.

Connection tuning is driven by settings:
    REDIS_MAX_CONNECTIONS   (default 50)
    REDIS_SOCKET_TIMEOUT    (default 3.0 s)
    REDIS_CONNECT_TIMEOUT   (default 3.0 s)
"""
from __future__ import annotations

import logging
from typing import Optional

from app.core.config import settings

logger = logging.getLogger(__name__)

_client: Optional[object] = None  # redis.asyncio.Redis when available
_init_attempted: bool = False
_import_warned: bool = False


async def get_redis() -> Optional[object]:
    """Return a connected async Redis client, or ``None`` if Redis is disabled.

    Idempotent: the first call connects; subsequent calls reuse the singleton.
    If connecting fails we log at WARNING and return ``None`` so the caller
    transparently falls back to in-process state.
    """
    global _client, _init_attempted, _import_warned

    if not settings.REDIS_URL:
        return None

    if _client is not None:
        return _client

    if _init_attempted and _client is None:
        # We tried and failed; don't hammer the server on every request.
        # Call reset_connection() to allow a retry (e.g. from a health-check loop).
        return None

    _init_attempted = True
    try:
        from redis.asyncio import from_url  # type: ignore
    except ImportError:
        if not _import_warned:
            logger.warning(
                "REDIS_URL is set but the 'redis' package is not installed. "
                "Falling back to in-process state. Add `redis>=5.0` to "
                "requirements.txt to enable shared Redis state."
            )
            _import_warned = True
        return None

    try:
        _client = from_url(
            settings.REDIS_URL,
            encoding="utf-8",
            decode_responses=True,
            max_connections=settings.REDIS_MAX_CONNECTIONS,
            socket_connect_timeout=settings.REDIS_CONNECT_TIMEOUT,
            socket_timeout=settings.REDIS_SOCKET_TIMEOUT,
            health_check_interval=30,
        )
        # Lightweight connectivity probe — surfaces auth / DNS errors at boot.
        await _client.ping()  # type: ignore[attr-defined]
        logger.info(
            "Redis client connected (prefix=%s, pool_max=%d)",
            settings.REDIS_KEY_PREFIX,
            settings.REDIS_MAX_CONNECTIONS,
        )
        return _client
    except Exception as e:
        logger.warning("Redis connection failed (%s); falling back to in-process state", e)
        _client = None
        return None


def reset_connection() -> None:
    """Allow get_redis() to attempt reconnection on the next call.

    Intended for use in health-check / background reconnect loops after a
    transient Redis failure.  Safe to call from any coroutine context.
    """
    global _client, _init_attempted
    _client = None
    _init_attempted = False


def key(*parts: str) -> str:
    """Build a namespaced Redis key.

    All keys MUST be constructed via this helper so that a single
    ``REDIS_KEY_PREFIX`` change isolates environments sharing one Redis
    instance (e.g. ``mela-prod:`` vs ``mela-dev:``).
    """
    return settings.REDIS_KEY_PREFIX + ":".join(p for p in parts if p)


async def close_redis() -> None:
    """Cleanly close the singleton connection pool on app shutdown."""
    global _client
    if _client is not None:
        try:
            await _client.aclose()  # type: ignore[attr-defined]
        except Exception as e:
            logger.warning("Redis close failed: %s", e)
        _client = None
