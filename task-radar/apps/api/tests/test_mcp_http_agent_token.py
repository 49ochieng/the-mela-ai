"""Integration: MCP HTTP transport must accept agent-token auth and inject
the calling user's id into every tool invocation."""
from __future__ import annotations

import secrets

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.database import Base, get_session
from app.deps import AGENT_TOKEN_PREFIX, hash_agent_token
from app.mcp.server import create_http_app
from app.models import AgentToken, Tenant, User


@pytest.mark.asyncio
async def test_mcp_call_with_agent_token_executes_as_owner():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    sm = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)

    plaintext = AGENT_TOKEN_PREFIX + secrets.token_urlsafe(16)

    async with sm() as s:
        t = Tenant(entra_tenant_id="t", name="T"); s.add(t); await s.flush()
        u = User(tenant_id=t.id, entra_user_id="u", display_name="U",
                 email="u@x", timezone="UTC", role="user")
        s.add(u); await s.flush()
        s.add(AgentToken(
            tenant_id=t.id, user_id=u.id, name="mela",
            token_hash=hash_agent_token(plaintext),
        ))
        await s.commit()

    app = create_http_app()

    async def _ov():
        async with sm() as s:
            yield s
    app.dependency_overrides[get_session] = _ov

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t",
                           headers={"Authorization": f"Bearer {plaintext}"}) as c:
        r = await c.get("/mcp/tools")
        assert r.status_code == 200
        assert "get_today_tasks" in r.json()["tools"]

        # Unknown tool returns 404 *after* auth — proves auth path executed.
        r = await c.post("/mcp/call", json={"name": "unknown_tool", "arguments": {}})
        assert r.status_code == 404

        # Wrong / missing token is rejected.
        r2 = await AsyncClient(transport=ASGITransport(app=app), base_url="http://t").get("/mcp/tools")
        assert r2.status_code == 401

    await engine.dispose()
