"""Phase 1.3 — agent token lifecycle: create, list, use, revoke, expire.

Agent tokens are how Mela / MCP / external automation acts on behalf of a
specific signed-in user. Properties locked in here:

- Plaintext is shown once and never stored (only sha256 hash is persisted).
- Token can authenticate Mela/MCP endpoints as that user.
- Revocation immediately blocks subsequent calls.
- An agent token cannot mint another agent token (defense against pivot).
- Tokens are scoped to one user; another user cannot list/revoke them.
"""
from __future__ import annotations

from datetime import datetime, timedelta

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.config import get_settings
from app.database import Base, get_session
from app.deps import AGENT_TOKEN_PREFIX, hash_agent_token
from app.main import create_app
from app.models import AgentToken, Tenant, User
from app.services.auth.sessions import issue_session


async def _seed_two_users():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    sm = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
    async with sm() as s:
        t = Tenant(entra_tenant_id="t", name="T"); s.add(t); await s.flush()
        a = User(tenant_id=t.id, entra_user_id="a", display_name="A",
                 email="a@x", timezone="UTC", role="user")
        b = User(tenant_id=t.id, entra_user_id="b", display_name="B",
                 email="b@x", timezone="UTC", role="user")
        s.add_all([a, b]); await s.commit()
        a_id, b_id, t_id = a.id, b.id, t.id

    app = create_app()

    async def _ov():
        async with sm() as s:
            yield s
    app.dependency_overrides[get_session] = _ov
    return app, sm, a_id, b_id, t_id, engine


@pytest.mark.asyncio
async def test_agent_token_full_lifecycle():
    app, sm, a_id, b_id, t_id, engine = await _seed_two_users()
    try:
        s = get_settings()
        async with sm() as ss:
            a_cookie, _ = await issue_session(ss, user_id=a_id, tenant_id=t_id)

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t",
                               cookies={s.effective_cookie_name: a_cookie}) as c:
            # Create
            r = await c.post("/api/agent-tokens", json={"name": "mela", "expires_days": 30})
            assert r.status_code == 201, r.text
            body = r.json()
            token = body["token"]
            tok_id = body["id"]
            assert token.startswith(AGENT_TOKEN_PREFIX)
            assert body["expires_at"] is not None

            # The plaintext is NOT persisted — only the hash matches.
            async with sm() as ss:
                row = await ss.get(AgentToken, tok_id)
                assert row is not None
                assert row.token_hash == hash_agent_token(token)
                assert "token" not in {c.name for c in AgentToken.__table__.columns}

            # List
            r = await c.get("/api/agent-tokens")
            assert r.status_code == 200
            assert any(item["id"] == tok_id for item in r.json())

        # The token authenticates as user A on a non-cookie call.
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
            r = await c.get("/api/me", headers={"Authorization": f"Bearer {token}"})
            assert r.status_code == 200
            assert r.json()["email"] == "a@x"

        # An agent token cannot mint another agent token.
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
            r = await c.post("/api/agent-tokens",
                             headers={"Authorization": f"Bearer {token}"},
                             json={"name": "pivot"})
            assert r.status_code == 403

        # User B cannot see / revoke A's token.
        async with sm() as ss:
            b_cookie, _ = await issue_session(ss, user_id=b_id, tenant_id=t_id)
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t",
                               cookies={s.effective_cookie_name: b_cookie}) as c:
            r = await c.get("/api/agent-tokens")
            assert r.status_code == 200
            assert all(item["id"] != tok_id for item in r.json())
            r = await c.delete(f"/api/agent-tokens/{tok_id}")
            assert r.status_code == 404

        # Owner revokes the token.
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t",
                               cookies={s.effective_cookie_name: a_cookie}) as c:
            r = await c.delete(f"/api/agent-tokens/{tok_id}")
            assert r.status_code == 204

        # Revoked token can no longer authenticate.
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
            r = await c.get("/api/me", headers={"Authorization": f"Bearer {token}"})
            assert r.status_code == 401

    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_agent_token_expiry_blocks_use():
    app, sm, a_id, _b, t_id, engine = await _seed_two_users()
    try:
        s = get_settings()
        async with sm() as ss:
            a_cookie, _ = await issue_session(ss, user_id=a_id, tenant_id=t_id)

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t",
                               cookies={s.effective_cookie_name: a_cookie}) as c:
            r = await c.post("/api/agent-tokens", json={"name": "short", "expires_days": 1})
            assert r.status_code == 201
            token = r.json()["token"]
            tok_id = r.json()["id"]

        # Force expiry in the past.
        async with sm() as ss:
            row = await ss.get(AgentToken, tok_id)
            row.expires_at = datetime.utcnow() - timedelta(seconds=1)
            await ss.commit()

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
            r = await c.get("/api/me", headers={"Authorization": f"Bearer {token}"})
            assert r.status_code == 401

    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_unknown_agent_token_is_unauthorized():
    app, _sm, *_rest, engine = await _seed_two_users()
    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
            r = await c.get("/api/me", headers={
                "Authorization": f"Bearer {AGENT_TOKEN_PREFIX}deadbeefnotreal"
            })
            assert r.status_code == 401
    finally:
        await engine.dispose()
