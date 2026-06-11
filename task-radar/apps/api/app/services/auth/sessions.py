"""Server-side session lifecycle.

Backs every issued JWT with a row in ``sessions`` so we can:

* revoke a single session (logout),
* "sign out everywhere" (revoke all of a user's sessions in one call), and
* surface an active-session list in the UI without trusting the client.

Hashing IP and user-agent (instead of storing raw values) gives users
visibility ("session from IP …a3f2…") without persisting PII.
"""
from __future__ import annotations

import hashlib
from datetime import datetime
from typing import Optional

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from ...models import Session as SessionRow
from ...utils.jwt import create_session_token


def _h(v: Optional[str]) -> Optional[str]:
    if not v:
        return None
    return hashlib.sha256(v.encode("utf-8", errors="ignore")).hexdigest()


async def issue_session(
    db: AsyncSession,
    *,
    user_id: str,
    tenant_id: str,
    ip: Optional[str] = None,
    user_agent: Optional[str] = None,
) -> tuple[str, datetime]:
    """Mint a JWT and persist its ``jti`` in the sessions table.

    Returns ``(token, expires_at)`` so the caller can set the cookie.
    """
    token, jti, exp = create_session_token(user_id=user_id, tenant_id=tenant_id)
    db.add(SessionRow(
        tenant_id=tenant_id,
        user_id=user_id,
        jti=jti,
        issued_at=datetime.utcnow(),
        last_seen_at=datetime.utcnow(),
        expires_at=exp.replace(tzinfo=None),
        ip_hash=_h(ip),
        ua_hash=_h(user_agent),
    ))
    await db.commit()
    return token, exp


async def get_active_session(db: AsyncSession, jti: str) -> Optional[SessionRow]:
    """Return the row for ``jti`` iff it is active (not revoked, not expired)."""
    row = (await db.execute(select(SessionRow).where(SessionRow.jti == jti))).scalar_one_or_none()
    if row is None:
        return None
    now = datetime.utcnow()
    if row.revoked_at is not None or row.expires_at < now:
        return None
    return row


async def touch_session(db: AsyncSession, jti: str) -> None:
    await db.execute(
        update(SessionRow)
        .where(SessionRow.jti == jti, SessionRow.revoked_at.is_(None))
        .values(last_seen_at=datetime.utcnow())
    )
    await db.commit()


async def revoke_session(db: AsyncSession, jti: str) -> None:
    await db.execute(
        update(SessionRow)
        .where(SessionRow.jti == jti, SessionRow.revoked_at.is_(None))
        .values(revoked_at=datetime.utcnow())
    )
    await db.commit()


async def revoke_all_for_user(db: AsyncSession, user_id: str) -> int:
    """Sign out everywhere. Returns number of sessions revoked."""
    res = await db.execute(
        update(SessionRow)
        .where(SessionRow.user_id == user_id, SessionRow.revoked_at.is_(None))
        .values(revoked_at=datetime.utcnow())
    )
    await db.commit()
    return int(res.rowcount or 0)
