"""Health, readiness and MCP HTTP integration tests."""
from __future__ import annotations

import pytest
from httpx import ASGITransport, AsyncClient

from app.main import create_app
from app.mcp.server import create_http_app


@pytest.mark.asyncio
async def test_health_ok():
    app = create_app()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
        r = await c.get("/health")
    assert r.status_code == 200
    assert r.json() == {"status": "ok"}


@pytest.mark.asyncio
async def test_mcp_requires_authentication():
    """MCP HTTP transport now requires per-user auth (cookie or agent token).
    A bare request without credentials must be rejected."""
    app = create_http_app()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
        r = await c.get("/mcp/tools")
    assert r.status_code == 401
