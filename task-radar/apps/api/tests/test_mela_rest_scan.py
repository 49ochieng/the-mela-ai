"""POST /mela/tools/scan must persist a QUEUED ScanRun for the caller."""
from __future__ import annotations

import pytest
from sqlalchemy import select

from app.deps import RequestContext
from app.enums import ScanStatus, ScanType
from app.models import ScanRun, Tenant, User
from app.routers.mela import scan
from app.schemas import RunScanRequest


async def _seed(session) -> RequestContext:
    t = Tenant(entra_tenant_id="t", name="A"); session.add(t); await session.flush()
    u = User(tenant_id=t.id, entra_user_id="u", display_name="A",
             email="a@x.com", timezone="UTC", role="user")
    session.add(u); await session.commit()
    return RequestContext(user=u, tenant_id=t.id)


@pytest.mark.asyncio
async def test_mela_rest_scan_creates_queued_scanrun(session, monkeypatch):
    ctx = await _seed(session)

    enqueued: list[dict] = []

    class _FakeQ:
        async def enqueue(self, msg): enqueued.append(msg)

    monkeypatch.setattr("app.routers.mela.get_queue", lambda: _FakeQ())

    resp = await scan(
        payload=RunScanRequest(source=ScanType.EMAIL, lookback_hours=24,
                               include_attachments=True),
        ctx=ctx, session=session,
    )

    assert resp.status == ScanStatus.QUEUED
    sr = (await session.execute(select(ScanRun))).scalars().first()
    assert sr is not None
    assert sr.status == ScanStatus.QUEUED.value
    assert sr.scan_type == "email"
    assert sr.user_id == ctx.user_id
    assert sr.source_scope.get("include_attachments") is True
    assert sr.source_scope.get("lookback_hours") == 24
    assert enqueued and enqueued[0]["scan_run_id"] == sr.id
