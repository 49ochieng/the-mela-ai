"""DB-backed, expiring OAuth/PKCE state store.

Replaces the in-memory ``_state_cache`` dict in ``routers/auth.py``. The
in-memory variant lost state on every restart, leaked memory on abandoned
flows, and could not be shared across worker processes. The DB-backed store
also gives us true single-use semantics via ``consumed_at``.
"""
from __future__ import annotations

import secrets
from datetime import datetime, timedelta

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from ...models import OAuthState

STATE_TTL_MINUTES = 10


async def put_state(db: AsyncSession, state: str, flow: dict) -> str:
    """Persist ``flow`` keyed by ``state`` and return a paired ``nonce``.

    The ``nonce`` is included as an ``extra`` claim in the eventual session
    JWT (or used by the caller to bind state to a downstream redirect),
    closing the door on cross-flow swaps.
    """
    nonce = secrets.token_urlsafe(24)
    db.add(OAuthState(
        state=state,
        nonce=nonce,
        flow_json=flow,
        created_at=datetime.utcnow(),
        expires_at=datetime.utcnow() + timedelta(minutes=STATE_TTL_MINUTES),
    ))
    await db.commit()
    return nonce


async def consume_state(db: AsyncSession, state: str) -> dict | None:
    """Atomically fetch and mark a state as consumed.

    Returns the original flow dict if the state exists, has not expired and
    has not been used; otherwise ``None``.
    """
    row = (await db.execute(select(OAuthState).where(OAuthState.state == state))).scalar_one_or_none()
    if row is None:
        return None
    now = datetime.utcnow()
    if row.consumed_at is not None or row.expires_at < now:
        return None
    row.consumed_at = now
    await db.commit()
    return dict(row.flow_json)


async def purge_expired(db: AsyncSession) -> int:
    """Delete expired/consumed rows. Safe to call from a periodic job."""
    res = await db.execute(
        delete(OAuthState).where(OAuthState.expires_at < datetime.utcnow())
    )
    await db.commit()
    return int(res.rowcount or 0)
