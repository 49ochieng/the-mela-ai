"""Phase 5.3 — privacy / GDPR endpoints."""
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


async def _seed():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    sm = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
    async with sm() as s:
        t = Tenant(entra_tenant_id="t", name="T"); s.add(t); await s.flush()
        u = User(tenant_id=t.id, entra_user_id="u", display_name="U",
                 email="u@x", timezone="UTC", role="user")
        s.add(u); await s.flush()
        s.add(GraphConnection(
            tenant_id=t.id, user_id=u.id, provider="microsoft",
            scopes="Mail.Read", status="connected",
            token_reference="kv://vault/secrets/x", refresh_token_reference="kv://vault/secrets/y",
        ))
        s.add(AgentToken(
            tenant_id=t.id, user_id=u.id, name="mela",
            token_hash="h" * 64, expires_at=datetime.utcnow() + timedelta(days=30),
        ))
        await s.commit()
        u_id, t_id = u.id, t.id

    app = create_app()

    async def _ov():
        async with sm() as s:
            yield s
    app.dependency_overrides[get_session] = _ov
    return app, sm, u_id, t_id


@pytest.mark.asyncio
async def test_export_excludes_secrets_and_returns_user_rows():
    app, sm, u_id, t_id = await _seed()
    s_cfg = get_settings()
    async with sm() as ss:
        cookie, _ = await issue_session(ss, user_id=u_id, tenant_id=t_id)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t",
                           cookies={s_cfg.effective_cookie_name: cookie}) as c:
        r = await c.get("/api/me/export")
        assert r.status_code == 200, r.text
        body = r.json()
    assert body["user"]["email"] == "u@x"
    # Per-user tables present even if empty.
    for label in ("scan_runs", "tasks", "task_syncs", "source_messages",
                  "scan_settings", "scan_events", "task_attachments"):
        assert label in body
    # Connections exported, but token references stripped.
    assert len(body["graph_connections"]) == 1
    conn = body["graph_connections"][0]
    assert "token_reference" not in conn
    assert "refresh_token_reference" not in conn
    # Agent tokens exported, but the hash is never returned.
    assert len(body["agent_tokens"]) == 1
    assert "token_hash" not in body["agent_tokens"][0]


@pytest.mark.asyncio
async def test_delete_revokes_sessions_tokens_and_connections():
    app, sm, u_id, t_id = await _seed()
    s_cfg = get_settings()
    async with sm() as ss:
        cookie, _ = await issue_session(ss, user_id=u_id, tenant_id=t_id)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t",
                           cookies={s_cfg.effective_cookie_name: cookie}) as c:
        r = await c.post("/api/me/delete")
        assert r.status_code == 200, r.text
        body = r.json()
    assert body["status"] == "scheduled"
    assert body["sessions_revoked"] >= 1
    assert body["agent_tokens_revoked"] == 1
    assert body["connections_disconnected"] == 1
    assert body["grace_period_days"] == 30

    async with sm() as ss:
        sess = (await ss.execute(select(SessionModel))).scalars().all()
        toks = (await ss.execute(select(AgentToken))).scalars().all()
        conns = (await ss.execute(select(GraphConnection))).scalars().all()
    assert all(s.revoked_at is not None for s in sess)
    assert all(t.revoked_at is not None for t in toks)
    assert conns[0].status == "disconnected"
    assert conns[0].token_reference is None
    assert conns[0].refresh_token_reference is None

    # Subsequent request with same cookie is rejected (session revoked).
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t",
                           cookies={s_cfg.effective_cookie_name: cookie}) as c:
        r = await c.get("/api/me")
    assert r.status_code == 401
