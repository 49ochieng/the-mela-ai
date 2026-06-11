"""Phase 2 — server-side session lifecycle.

Verifies:
- A session JWT is paired with a row in the ``sessions`` table.
- Logout revokes that row; the cookie can no longer authenticate.
- ``/api/auth/logout-all`` revokes every active session for the user.
- A JWT whose ``jti`` has no matching active row is rejected (401).
- The DB-backed OAuth state store is single-use and expires.
"""
from __future__ import annotations

from datetime import datetime, timedelta

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.config import get_settings
from app.database import Base, get_session
from app.main import create_app
from app.models import OAuthState, Session as SessionRow, Tenant, User
from app.services.auth.oauth_state import consume_state, put_state
from app.services.auth.sessions import issue_session, revoke_all_for_user


async def _setup():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    sm = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
    async with sm() as s:
        t = Tenant(entra_tenant_id="t", name="T"); s.add(t); await s.flush()
        u = User(tenant_id=t.id, entra_user_id="u", display_name="U",
                 email="u@x", timezone="UTC", role="user")
        s.add(u); await s.commit()
        uid, tid = u.id, t.id
    app = create_app()

    async def _ov():
        async with sm() as s:
            yield s
    app.dependency_overrides[get_session] = _ov
    return app, sm, uid, tid, engine


@pytest.mark.asyncio
async def test_logout_revokes_server_side_session():
    app, sm, uid, tid, engine = await _setup()
    try:
        s = get_settings()
        async with sm() as ss:
            token, _ = await issue_session(ss, user_id=uid, tenant_id=tid)
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t",
                               cookies={s.effective_cookie_name: token}) as c:
            r = await c.get("/api/me")
            assert r.status_code == 200
            r = await c.post("/api/auth/logout")
            assert r.status_code == 200
            # The cookie value (still in the client jar pre-Set-Cookie) no
            # longer authenticates because its jti is revoked server-side.
            r = await c.get("/api/me", cookies={s.effective_cookie_name: token})
            assert r.status_code == 401
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_logout_all_revokes_every_active_session():
    app, sm, uid, tid, engine = await _setup()
    try:
        s = get_settings()
        async with sm() as ss:
            t1, _ = await issue_session(ss, user_id=uid, tenant_id=tid, user_agent="a")
            t2, _ = await issue_session(ss, user_id=uid, tenant_id=tid, user_agent="b")
            t3, _ = await issue_session(ss, user_id=uid, tenant_id=tid, user_agent="c")
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t",
                               cookies={s.effective_cookie_name: t1}) as c:
            r = await c.post("/api/auth/logout-all")
            assert r.status_code == 200
            assert r.json()["revoked"] >= 3
        # All three previously valid tokens are now dead.
        for t in (t1, t2, t3):
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t",
                                   cookies={s.effective_cookie_name: t}) as c:
                r = await c.get("/api/me")
                assert r.status_code == 401
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_jwt_without_matching_session_is_rejected():
    """A JWT whose jti was wiped from the DB (e.g. admin revoked it directly)
    must not be accepted, even though the JWT is still cryptographically
    valid and unexpired."""
    app, sm, uid, tid, engine = await _setup()
    try:
        s = get_settings()
        async with sm() as ss:
            token, _ = await issue_session(ss, user_id=uid, tenant_id=tid)
            # Simulate admin-side mass revoke.
            await revoke_all_for_user(ss, uid)
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t",
                               cookies={s.effective_cookie_name: token}) as c:
            r = await c.get("/api/me")
            assert r.status_code == 401
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_oauth_state_is_single_use_and_expires():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    sm = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
    try:
        async with sm() as ss:
            nonce = await put_state(ss, "abc123", {"flow": "x"})
            assert nonce
        # First consume succeeds; second returns None (single-use).
        async with sm() as ss:
            assert await consume_state(ss, "abc123") == {"flow": "x"}
        async with sm() as ss:
            assert await consume_state(ss, "abc123") is None

        # Expired states cannot be consumed.
        async with sm() as ss:
            await put_state(ss, "expired", {"flow": "y"})
            row = (await ss.execute(select(OAuthState).where(OAuthState.state == "expired"))).scalar_one()
            row.expires_at = datetime.utcnow() - timedelta(seconds=1)
            await ss.commit()
        async with sm() as ss:
            assert await consume_state(ss, "expired") is None
    finally:
        await engine.dispose()
