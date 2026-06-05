"""Redis-backed session cache for Mela AI.

Sits in front of the ``user_sessions`` DB table to eliminate a DB read + write
on every authenticated request.  The DB remains the authoritative source of
truth for session state; Redis is a fast, TTL-governed projection of that truth.

Key schema:
    ``{PREFIX}session:{jti}``  → JSON string:
        {
          "id":               <session row UUID>,
          "user_id":          <internal user UUID>,
          "expires_at":       <ISO-8601 UTC>,
          "revoked_at":       <ISO-8601 UTC | null>,
          "last_activity_at": <ISO-8601 UTC>
        }

Security invariants enforced here:
  - ``revoke_session_by_jti`` MUST write the DB first and only then call
    ``invalidate_session_cache``.  The reverse order would open a TOCTOU window
    where a concurrent request reads an apparently valid cached entry after the
    DB has marked it revoked.
  - Cache entries are never trusted without validating ``revoked_at`` and
    ``expires_at`` against wall-clock time.  A corrupted entry falls back to a
    DB re-fetch rather than granting access.
"""
from __future__ import annotations

import json
import logging
from datetime import datetime, timedelta
from typing import Optional

from app.core.redis_client import get_redis, key as rkey

logger = logging.getLogger(__name__)

# TTL = absolute lifetime ceiling + 1 hour buffer so the key never expires
# before the session itself does (SESSION_ABSOLUTE_LIFETIME_HOURS default 12).
_SESSION_CACHE_TTL_SECONDS = 13 * 3600  # 13 hours


def _session_cache_key(token_jti: str) -> str:
    return rkey("session", token_jti)


async def get_session_from_cache(token_jti: str) -> Optional[dict]:
    """Return the cached session dict, or ``None`` on miss / Redis unavailable.

    Always validate ``revoked_at`` and ``expires_at`` on the returned dict
    before trusting it.
    """
    r = await get_redis()
    if r is None:
        return None
    try:
        raw = await r.get(_session_cache_key(token_jti))
        if not raw:
            return None
        data = json.loads(raw)
        # Sanity-check required fields — treat malformed entries as a miss.
        if not isinstance(data, dict) or "user_id" not in data or "expires_at" not in data:
            logger.warning("session_cache: malformed entry for jti %s; treating as miss", token_jti[:16])
            await r.delete(_session_cache_key(token_jti))
            return None
        return data
    except Exception as exc:
        logger.warning("session_cache get error (%s); falling back to DB", exc)
        return None


async def set_session_cache(
    token_jti: str,
    data: dict,
    ttl_seconds: int = _SESSION_CACHE_TTL_SECONDS,
) -> None:
    """Write session data to Redis.  Called after the DB row is committed."""
    r = await get_redis()
    if r is None:
        return
    try:
        await r.set(_session_cache_key(token_jti), json.dumps(data), ex=ttl_seconds)
    except Exception as exc:
        logger.warning("session_cache set error (%s); continuing without cache", exc)


async def invalidate_session_cache(token_jti: str) -> None:
    """Delete the cached session entry.

    MUST be called AFTER the DB row is marked revoked, not before.
    """
    r = await get_redis()
    if r is None:
        return
    try:
        await r.delete(_session_cache_key(token_jti))
    except Exception as exc:
        logger.warning("session_cache invalidate error (%s); DB is still authoritative", exc)


async def touch_session_cache(token_jti: str) -> bool:
    """Update ``last_activity_at`` in the cached entry and slide the TTL.

    Returns ``True`` if the entry existed and was updated, ``False`` on a miss.
    A miss means the caller must fall through to a DB touch.
    """
    r = await get_redis()
    if r is None:
        return False
    try:
        k = _session_cache_key(token_jti)
        raw = await r.get(k)
        if not raw:
            return False
        data = json.loads(raw)
        data["last_activity_at"] = datetime.utcnow().isoformat()
        # keepttl=True: slide TTL is intentionally NOT done here — we preserve
        # the original absolute-expiry TTL so the key dies with the session.
        await r.set(k, json.dumps(data), keepttl=True)
        return True
    except Exception as exc:
        logger.warning("session_cache touch error (%s); falling back to DB touch", exc)
        return False


async def invalidate_all_user_sessions_cache(token_jtis: list[str]) -> None:
    """Bulk-invalidate a list of JTIs (used by revoke_all_user_sessions).

    Errors are non-fatal — the DB revocations are the authoritative source.
    """
    r = await get_redis()
    if r is None or not token_jtis:
        return
    try:
        keys = [_session_cache_key(jti) for jti in token_jtis]
        await r.delete(*keys)
    except Exception as exc:
        logger.warning("session_cache bulk invalidate error (%s); DB is still authoritative", exc)
