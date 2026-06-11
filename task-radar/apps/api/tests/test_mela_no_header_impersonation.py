"""Phase 1.2 — header-based caller impersonation must be removed.

Previously a request that supplied ``X-Mela-Api-Key`` + ``X-Mela-User-Id``
was accepted as that user. With the new auth model the only ways to call
``/api/mela/*`` are (a) a valid session cookie/JWT or (b) a per-user agent
token. Anything else must be 401.
"""
from __future__ import annotations

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.database import Base, get_session
from app.main import create_app
from app.models import Tenant, User


async def _seed():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    sm = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
    async with sm() as s:
        t = Tenant(entra_tenant_id="t", name="T"); s.add(t); await s.flush()
        u = User(tenant_id=t.id, entra_user_id="u", display_name="U",
                 email="u@x", timezone="UTC", role="user")
        s.add(u); await s.commit()
        uid = u.id
    app = create_app()

    async def _ov():
        async with sm() as s:
            yield s
    app.dependency_overrides[get_session] = _ov
    return app, uid, engine


@pytest.mark.asyncio
async def test_mela_rejects_x_mela_user_id_header():
    app, uid, engine = await _seed()
    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
            # Old impersonation pattern: arbitrary user_id + a "service key".
            r = await c.get("/api/mela/tools/brief", headers={
                "X-Mela-User-Id": uid,
                "X-Mela-Api-Key": "anything",
            })
        assert r.status_code == 401
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_mela_brief_requires_auth():
    app, _uid, engine = await _seed()
    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
            r = await c.get("/api/mela/tools/brief")
        assert r.status_code == 401
    finally:
        await engine.dispose()
