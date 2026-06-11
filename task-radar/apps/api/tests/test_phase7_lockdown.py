"""Phase 7 — emergency lockdown wipes all sessions / tokens / connections."""
from __future__ import annotations

from datetime import datetime, timedelta

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.config import get_settings
from app.database import Base, get_session
from app.main import create_app
from app.models import (
    AgentToken,
    GraphConnection,
    Session as SessionModel,
    Tenant,
    User,
)
from app.services.auth.sessions import issue_session


async def _seed_two_tenants():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    sm = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
    async with sm() as s:
        t1 = Tenant(entra_tenant_id="t1", name="T1"); t2 = Tenant(entra_tenant_id="t2", name="T2")
        s.add_all([t1, t2]); await s.flush()
        admin = User(tenant_id=t1.id, entra_user_id="admin", display_name="A",
                     email="admin@x", timezone="UTC", role="admin")
        u1 = User(tenant_id=t1.id, entra_user_id="u1", display_name="U1",
                  email="u1@x", timezone="UTC", role="user")
        u2 = User(tenant_id=t2.id, entra_user_id="u2", display_name="U2",
                  email="u2@x", timezone="UTC", role="user")
        s.add_all([admin, u1, u2]); await s.flush()
        for idx, u in enumerate((u1, u2)):
            s.add(GraphConnection(
                tenant_id=u.tenant_id, user_id=u.id, provider="microsoft",
                scopes="Mail.Read", status="connected",
                token_reference="kv://x", refresh_token_reference="kv://y"))
            s.add(AgentToken(
                tenant_id=u.tenant_id, user_id=u.id, name="t",
                token_hash=f"{idx}" + "h" * 63,
                expires_at=datetime.utcnow() + timedelta(days=30)))
        await s.commit()
        return engine, sm, admin.id, t1.id, u1.id, u2.id


@pytest.mark.asyncio
async def test_lockdown_revokes_everything_globally():
    engine, sm, admin_id, t1_id, u1_id, u2_id = await _seed_two_tenants()
    app = create_app()

    async def _ov():
        async with sm() as s:
            yield s
    app.dependency_overrides[get_session] = _ov
    s_cfg = get_settings()
    async with sm() as ss:
        admin_cookie, _ = await issue_session(ss, user_id=admin_id, tenant_id=t1_id)
        await issue_session(ss, user_id=u1_id, tenant_id=t1_id)
        await issue_session(ss, user_id=u2_id, tenant_id="t2-id-placeholder")

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t",
                           cookies={s_cfg.effective_cookie_name: admin_cookie}) as c:
        r = await c.post("/api/admin/security/lockdown")
        assert r.status_code == 200, r.text
        body = r.json()
    assert body["sessions_revoked"] >= 3
    assert body["agent_tokens_revoked"] == 2
    assert body["graph_connections_disconnected"] == 2

    async with sm() as ss:
        sess = (await ss.execute(select(SessionModel))).scalars().all()
        toks = (await ss.execute(select(AgentToken))).scalars().all()
        conns = (await ss.execute(select(GraphConnection))).scalars().all()
    assert all(s.revoked_at is not None for s in sess)
    assert all(t.revoked_at is not None for t in toks)
    assert all(c.status == "disconnected" and c.token_reference is None for c in conns)


@pytest.mark.asyncio
async def test_lockdown_requires_admin():
    engine, sm, _admin_id, t1_id, u1_id, _u2 = await _seed_two_tenants()
    app = create_app()

    async def _ov():
        async with sm() as s:
            yield s
    app.dependency_overrides[get_session] = _ov
    s_cfg = get_settings()
    async with sm() as ss:
        u_cookie, _ = await issue_session(ss, user_id=u1_id, tenant_id=t1_id)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t",
                           cookies={s_cfg.effective_cookie_name: u_cookie}) as c:
        r = await c.post("/api/admin/security/lockdown")
    assert r.status_code == 403
