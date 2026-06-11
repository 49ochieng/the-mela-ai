"""Phase 1.1 — MCP get_scan_status must enforce per-user ownership.

A user must never receive scan metrics belonging to another user, even within
the same tenant. The previous implementation looked up the ScanRun by id with
no ownership check at all.
"""
from __future__ import annotations

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.database import Base
from app.enums import ScanStatus
from app.mcp.server import tool_get_scan_status
from app.models import ScanRun, Tenant, User


@pytest.mark.asyncio
async def test_get_scan_status_rejects_cross_user(monkeypatch):
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    sm = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)

    # Patch session_scope used inside MCP tools so they hit our isolated DB.
    from contextlib import asynccontextmanager

    @asynccontextmanager
    async def _scope():
        async with sm() as s:
            try:
                yield s
                await s.commit()
            except Exception:
                await s.rollback()
                raise

    monkeypatch.setattr("app.mcp.server.session_scope", _scope)

    # Two users in the same tenant + a scan owned by user A.
    async with sm() as s:
        t = Tenant(entra_tenant_id="t1", name="T1"); s.add(t); await s.flush()
        a = User(tenant_id=t.id, entra_user_id="a", display_name="A",
                 email="a@x", timezone="UTC", role="user")
        b = User(tenant_id=t.id, entra_user_id="b", display_name="B",
                 email="b@x", timezone="UTC", role="user")
        s.add_all([a, b]); await s.flush()
        sr = ScanRun(tenant_id=t.id, user_id=a.id,
                     status=ScanStatus.COMPLETED.value, scan_type="all")
        s.add(sr); await s.commit()
        a_id, b_id, sr_id = a.id, b.id, sr.id

    # Owner can read.
    out = await tool_get_scan_status({"user_id": a_id, "scan_run_id": sr_id})
    assert out["status"] == ScanStatus.COMPLETED.value

    # Other user in same tenant must be rejected.
    with pytest.raises(ValueError, match="scan not found"):
        await tool_get_scan_status({"user_id": b_id, "scan_run_id": sr_id})

    # Missing user_id is rejected.
    with pytest.raises(ValueError, match="user_id is required"):
        await tool_get_scan_status({"scan_run_id": sr_id})

    await engine.dispose()
