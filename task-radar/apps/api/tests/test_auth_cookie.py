"""Cookie-based session auth contract tests.

Locks in:
- Protected routes return 401 when no cookie/bearer is present
- /api/me returns the user when a valid session cookie is set
- /api/auth/logout clears the cookie
- Bearer fallback still works for MCP/programmatic clients
"""
from __future__ import annotations

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.config import get_settings
from app.database import Base, get_session
from app.main import create_app
from app.models import Tenant, User
from app.services.auth.sessions import issue_session


async def _build_app_with_seeded_user():
    """Build a FastAPI app whose get_session points at an isolated in-memory
    engine that already contains a seeded user, so handlers can see it."""
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    sm = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
    async with sm() as s:
        tenant = Tenant(entra_tenant_id="cookie-tenant", name="Cookie Co")
        s.add(tenant)
        await s.flush()
        user = User(
            tenant_id=tenant.id, entra_user_id="cookie-user",
            display_name="Cookie User", email="cookie@example.com",
            timezone="UTC", role="user",
        )
        s.add(user)
        await s.commit()
        user_id, tenant_id = user.id, tenant.id

    app = create_app()

    async def _override():
        async with sm() as s:
            yield s

    app.dependency_overrides[get_session] = _override
    return app, sm, user_id, tenant_id, engine


@pytest.mark.asyncio
async def test_me_requires_authentication():
    app = create_app()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
        r = await c.get("/api/me")
    assert r.status_code == 401


@pytest.mark.asyncio
async def test_me_returns_user_with_session_cookie():
    app, sm, user_id, tenant_id, engine = await _build_app_with_seeded_user()
    try:
        async with sm() as ss:
            token, _ = await issue_session(ss, user_id=user_id, tenant_id=tenant_id)
        s = get_settings()
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://t",
            cookies={s.effective_cookie_name: token},
        ) as c:
            r = await c.get("/api/me")
        assert r.status_code == 200
        body = r.json()
        assert body["email"] == "cookie@example.com"
        assert body["display_name"] == "Cookie User"
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_me_returns_user_with_bearer_fallback():
    app, sm, user_id, tenant_id, engine = await _build_app_with_seeded_user()
    try:
        async with sm() as ss:
            token, _ = await issue_session(ss, user_id=user_id, tenant_id=tenant_id)
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
            r = await c.get("/api/me", headers={"Authorization": f"Bearer {token}"})
        assert r.status_code == 200
        assert r.json()["email"] == "cookie@example.com"
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_logout_clears_cookie():
    s = get_settings()
    app = create_app()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
        r = await c.post("/api/auth/logout")
    assert r.status_code == 200
    set_cookie = r.headers.get("set-cookie", "")
    assert s.effective_cookie_name in set_cookie
    assert "max-age=0" in set_cookie.lower() or "expires=" in set_cookie.lower()

