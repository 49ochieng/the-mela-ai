"""
Mela AI — Per-user daily upload quota (M-4).

Mirrors the pattern in ``user_web_connector.py``: Redis fast-path with an
in-process fallback so multi-replica deployments stay consistent and
single-replica dev still works without Redis.

Quota window: UTC day. Key per user per day. Atomic check-and-consume.
Default: ``settings.DAILY_UPLOAD_QUOTA_MB == 0`` → unlimited (no quota check).
"""

from __future__ import annotations

import logging
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from typing import Tuple

from app.core.config import settings

logger = logging.getLogger(__name__)

# In-process fallback when Redis is unreachable.
# Shape: {user_id: {date: bytes_used}}.
_fallback_used: dict[str, dict] = defaultdict(lambda: defaultdict(int))


def _seconds_until_midnight_utc() -> int:
    now = datetime.now(timezone.utc)
    midnight = (now + timedelta(days=1)).replace(
        hour=0, minute=0, second=0, microsecond=0
    )
    return max(1, int((midnight - now).total_seconds()))


async def check_and_consume_upload_quota(
    user_id: str, file_size_bytes: int
) -> Tuple[bool, int, int]:
    """Atomically check + consume daily upload quota for one user.

    Returns (allowed, used_bytes_after, limit_bytes).

    - ``allowed=True``  → request may proceed; counter has been incremented.
    - ``allowed=False`` → quota exceeded; counter is NOT incremented.

    The counter is always denominated in BYTES; the configured limit is
    expressed in MB and converted on read so admins can tune it cleanly.

    When ``DAILY_UPLOAD_QUOTA_MB == 0`` the check is a no-op
    (returns ``(True, 0, 0)``).
    """
    limit_mb = int(getattr(settings, "DAILY_UPLOAD_QUOTA_MB", 0) or 0)
    if limit_mb <= 0:
        return True, 0, 0

    limit_bytes = limit_mb * 1024 * 1024
    if file_size_bytes <= 0:
        # Defensive — empty files don't consume quota.
        return True, 0, limit_bytes

    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")

    # ── Redis fast path ──────────────────────────────────────────────────────
    try:
        from app.core.redis_client import get_redis, key as rkey

        r = await get_redis()
        if r is not None:
            rk = rkey("quota", "upload", user_id, today)
            async with r.pipeline(transaction=True) as pipe:
                pipe.incrby(rk, file_size_bytes)
                pipe.ttl(rk)
                results = await pipe.execute()
            new_total, existing_ttl = results
            if existing_ttl < 0:
                await r.expire(rk, _seconds_until_midnight_utc())
            if new_total > limit_bytes:
                # Undo the over-consume.
                await r.decrby(rk, file_size_bytes)
                return False, new_total - file_size_bytes, limit_bytes
            return True, new_total, limit_bytes
    except Exception as exc:
        logger.debug(
            "upload quota Redis error (%s); falling back to in-process", exc
        )

    # ── In-process fallback ──────────────────────────────────────────────────
    today_date = datetime.now(timezone.utc).date()
    used = _fallback_used[user_id][today_date]
    if used + file_size_bytes > limit_bytes:
        return False, used, limit_bytes
    _fallback_used[user_id][today_date] = used + file_size_bytes
    return True, used + file_size_bytes, limit_bytes


async def release_upload_quota(user_id: str, file_size_bytes: int) -> None:
    """Decrement the counter if the upload was accepted by the quota gate
    but later failed (e.g. AV scan rejected, blob write failed).

    Always safe — never errors; never decrements below zero.
    """
    if file_size_bytes <= 0:
        return
    limit_mb = int(getattr(settings, "DAILY_UPLOAD_QUOTA_MB", 0) or 0)
    if limit_mb <= 0:
        return

    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    try:
        from app.core.redis_client import get_redis, key as rkey

        r = await get_redis()
        if r is not None:
            rk = rkey("quota", "upload", user_id, today)
            # decrby clamps to negative — we treat negative counts as 0 on read.
            await r.decrby(rk, file_size_bytes)
            return
    except Exception as exc:
        logger.debug("upload quota release Redis error: %s", exc)

    today_date = datetime.now(timezone.utc).date()
    used = _fallback_used[user_id][today_date]
    _fallback_used[user_id][today_date] = max(0, used - file_size_bytes)
