"""Session lifecycle helpers for Mela AI.

Provides server-side session record management used by:
  - get_current_user (active-user + revocation enforcement)
  - logout endpoint (revoke session)
  - admin disable / revoke-all (bulk revoke)
  - idle timeout middleware (touch last_activity_at)

Idle and absolute lifetime policies are read from settings:
  - SESSION_IDLE_TIMEOUT_MINUTES (default 30)
  - SESSION_ABSOLUTE_LIFETIME_HOURS (default 12)

Redis caching layer (app.core.session_cache) is used for fast-path lookups.
The DB remains authoritative; Redis is a TTL-governed projection.
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
import uuid
from datetime import datetime, timedelta
from typing import Optional

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings

logger = logging.getLogger(__name__)


def _idle_minutes() -> int:
    return int(getattr(settings, "SESSION_IDLE_TIMEOUT_MINUTES", 30) or 30)


def _absolute_hours() -> int:
    return int(getattr(settings, "SESSION_ABSOLUTE_LIFETIME_HOURS", 12) or 12)


def derive_jti(token: str, claim_jti: Optional[str] = None) -> str:
    """Derive a stable session identifier from the access token.

    Prefers the JWT `jti` claim when present; otherwise falls back to a
    SHA-256 of the raw token (truncated). This keeps the actual token out of
    the database.
    """
    if claim_jti:
        return claim_jti[:128]
    return "sha256:" + hashlib.sha256(token.encode("utf-8")).hexdigest()


async def get_or_create_session(
    db: AsyncSession,
    *,
    user_id: str,
    token_jti: str,
    token_exp: Optional[datetime] = None,
    ip_address: Optional[str] = None,
    user_agent: Optional[str] = None,
):
    """Return the active session row matching token_jti, creating one if missing.

    Fast path: checks Redis cache first.  On cache miss, falls back to DB.
    Newly created rows are written to both DB and cache.

    If the row exists but has been revoked or has expired, returns the row as-is
    (the caller must check `revoked_at` and `expires_at`).
    """
    from app.core.session_cache import get_session_from_cache, set_session_cache
    from app.models.models import UserSession

    # ── Fast path: Redis cache ────────────────────────────────────────────────
    cached = await get_session_from_cache(token_jti)
    if cached is not None:
        # Return a lightweight namespace object so callers use the same
        # attribute access pattern as with the ORM row.
        return _CachedSession(cached)

    # ── DB path ───────────────────────────────────────────────────────────────
    result = await db.execute(
        select(UserSession).where(UserSession.token_jti == token_jti)
    )
    sess = result.scalar_one_or_none()
    if sess is not None:
        # Populate cache for subsequent requests.
        await set_session_cache(token_jti, _session_to_dict(sess))
        return sess

    now = datetime.utcnow()
    absolute_exp = now + timedelta(hours=_absolute_hours())
    # Honor token's own exp if it is sooner than our absolute policy.
    if token_exp is not None and token_exp < absolute_exp:
        absolute_exp = token_exp

    sess = UserSession(
        id=str(uuid.uuid4()),
        user_id=user_id,
        token_jti=token_jti,
        issued_at=now,
        expires_at=absolute_exp,
        last_activity_at=now,
        revoked_at=None,
        ip_address=ip_address,
        user_agent=(user_agent or "")[:500] or None,
    )
    db.add(sess)
    try:
        await db.commit()
    except Exception:
        await db.rollback()
        # Race: another request created the same jti row first; re-fetch.
        result = await db.execute(
            select(UserSession).where(UserSession.token_jti == token_jti)
        )
        sess = result.scalar_one_or_none()
    if sess is not None:
        await set_session_cache(token_jti, _session_to_dict(sess))
    return sess


async def touch_session(db: AsyncSession, session_id: str, token_jti: Optional[str] = None) -> None:
    """Update last_activity_at for an active session.

    Redis is updated immediately (non-blocking fire-and-forget).
    DB update is performed asynchronously as a background task to avoid
    adding write latency to every authenticated request.
    """
    from app.core.session_cache import touch_session_cache

    # Fast Redis touch (non-blocking).
    if token_jti:
        cache_hit = await touch_session_cache(token_jti)
    else:
        cache_hit = False

    # Always persist to DB in the background.  If cache hit, schedule async;
    # if cache miss (cold start), write synchronously so the row stays fresh.
    async def _db_touch() -> None:
        from app.core.database import async_session_maker
        from app.models.models import UserSession
        try:
            async with async_session_maker() as new_db:
                await new_db.execute(
                    update(UserSession)
                    .where(UserSession.id == session_id)
                    .values(last_activity_at=datetime.utcnow())
                )
                await new_db.commit()
        except Exception as exc:
            logger.warning("touch_session DB write failed: %s", exc)

    if cache_hit:
        asyncio.create_task(_db_touch())
    else:
        await _db_touch()


async def revoke_session_by_jti(db: AsyncSession, token_jti: str) -> int:
    """Revoke (logout) the session matching token_jti. Returns rows affected.

    DB is written first (authoritative).  Cache is invalidated afterwards.
    The order is mandatory: reversing it creates a TOCTOU window.
    """
    from app.core.session_cache import invalidate_session_cache
    from app.models.models import UserSession

    res = await db.execute(
        update(UserSession)
        .where(
            UserSession.token_jti == token_jti,
            UserSession.revoked_at.is_(None),
        )
        .values(revoked_at=datetime.utcnow())
    )
    await db.commit()
    # Invalidate cache AFTER successful DB commit.
    await invalidate_session_cache(token_jti)
    return res.rowcount or 0


async def revoke_all_user_sessions(db: AsyncSession, user_id: str) -> int:
    """Revoke every active session for a user (admin disable / panic revoke).

    Fetches live JTIs first so the cache can be bulk-invalidated.
    DB is written first; cache invalidation is always secondary.
    """
    from app.core.session_cache import invalidate_all_user_sessions_cache
    from app.models.models import UserSession

    # Collect active JTIs before updating so we can invalidate their cache
    # entries.  (After the update, revoked_at is no longer None so a second
    # query would return nothing.)
    jti_result = await db.execute(
        select(UserSession.token_jti).where(
            UserSession.user_id == user_id,
            UserSession.revoked_at.is_(None),
        )
    )
    jtis = [row[0] for row in jti_result.fetchall()]

    res = await db.execute(
        update(UserSession)
        .where(
            UserSession.user_id == user_id,
            UserSession.revoked_at.is_(None),
        )
        .values(revoked_at=datetime.utcnow())
    )
    await db.commit()
    # Invalidate cache AFTER successful DB commit.
    if jtis:
        await invalidate_all_user_sessions_cache(jtis)
    return res.rowcount or 0


def session_is_valid(sess) -> tuple[bool, str]:
    """Return (ok, reason). reason is empty string when ok.

    Works for both ORM ``UserSession`` rows and ``_CachedSession`` objects.
    """
    if sess is None:
        return True, ""  # callers may treat missing rows as lazily-creatable
    now = datetime.utcnow()
    revoked_at = sess.revoked_at
    if isinstance(revoked_at, str):
        revoked_at = datetime.fromisoformat(revoked_at) if revoked_at else None
    if revoked_at is not None:
        return False, "session_revoked"
    expires_at = sess.expires_at
    if isinstance(expires_at, str):
        expires_at = datetime.fromisoformat(expires_at) if expires_at else None
    if expires_at is not None and now > expires_at:
        return False, "session_expired"
    last_activity_at = sess.last_activity_at
    if isinstance(last_activity_at, str):
        last_activity_at = datetime.fromisoformat(last_activity_at) if last_activity_at else None
    idle_cutoff = now - timedelta(minutes=_idle_minutes())
    if last_activity_at is not None and last_activity_at < idle_cutoff:
        return False, "session_idle_timeout"
    return True, ""


# ── Helpers ────────────────────────────────────────────────────────────────────


def _session_to_dict(sess) -> dict:
    """Serialise an ORM UserSession row to a plain dict for Redis storage."""
    def _iso(v) -> Optional[str]:
        if v is None:
            return None
        return v.isoformat() if hasattr(v, "isoformat") else str(v)

    return {
        "id": str(sess.id),
        "user_id": str(sess.user_id),
        "token_jti": str(sess.token_jti),
        "expires_at": _iso(sess.expires_at),
        "revoked_at": _iso(sess.revoked_at),
        "last_activity_at": _iso(sess.last_activity_at),
        "ip_address": str(sess.ip_address) if sess.ip_address else None,
    }


class _CachedSession:
    """Lightweight object mirroring ORM UserSession attribute access.

    Used when get_or_create_session returns a cached dict instead of an ORM
    row.  Attribute names must match the ORM model exactly.
    """

    __slots__ = ("id", "user_id", "token_jti", "expires_at", "revoked_at", "last_activity_at", "ip_address")

    def __init__(self, data: dict) -> None:
        self.id = data.get("id")
        self.user_id = data.get("user_id")
        self.token_jti = data.get("token_jti")
        # Keep as strings — session_is_valid() handles ISO-string conversion.
        self.expires_at = data.get("expires_at")
        self.revoked_at = data.get("revoked_at")
        self.last_activity_at = data.get("last_activity_at")
        self.ip_address = data.get("ip_address")
