"""Phase 5: tamper-evident audit chain + structured logging."""
from __future__ import annotations

import logging

import pytest

from app.services.tasks.audit import log, verify_chain


@pytest.mark.asyncio
async def test_chain_intact_after_multiple_writes(session):
    for i in range(5):
        await log(session, tenant_id="t1", user_id="u1", action=f"act.{i}",
                  details={"i": i})
    await session.commit()
    result = await verify_chain(session)
    assert result["ok"] is True
    assert result["checked"] == 5


@pytest.mark.asyncio
async def test_chain_detects_payload_tampering(session):
    from app.models import AuditLog
    from sqlalchemy import select
    for i in range(3):
        await log(session, tenant_id="t1", user_id="u1", action=f"act.{i}")
    await session.commit()
    # Silently mutate the second row's details — chain should break.
    rows = (await session.execute(select(AuditLog).order_by(AuditLog.seq))).scalars().all()
    rows[1].details_json = {"tampered": True}
    await session.commit()
    result = await verify_chain(session)
    assert result["ok"] is False
    assert result["broken_at"] == 2
    assert result["reason"] == "entry_hash mismatch"


@pytest.mark.asyncio
async def test_chain_detects_deletion(session):
    from app.models import AuditLog
    from sqlalchemy import delete, select
    for i in range(4):
        await log(session, tenant_id="t1", user_id="u1", action=f"act.{i}")
    await session.commit()
    # Delete entry seq=2, leaving a gap.
    await session.execute(delete(AuditLog).where(AuditLog.seq == 2))
    await session.commit()
    result = await verify_chain(session)
    assert result["ok"] is False
    assert "seq gap" in result["reason"]


@pytest.mark.asyncio
async def test_chain_each_row_links_to_previous(session):
    from app.models import AuditLog
    from sqlalchemy import select
    await log(session, tenant_id="t1", user_id="u1", action="a")
    await log(session, tenant_id="t1", user_id="u1", action="b")
    await session.commit()
    rows = (await session.execute(select(AuditLog).order_by(AuditLog.seq))).scalars().all()
    assert rows[0].prev_hash is None
    assert rows[1].prev_hash == rows[0].entry_hash
    assert rows[0].entry_hash and rows[1].entry_hash
    assert rows[0].entry_hash != rows[1].entry_hash


@pytest.mark.asyncio
async def test_audit_emits_structured_log(session):
    """Mirrored audit logger receives an `audit` record with the chain hash."""
    captured: list[logging.LogRecord] = []

    class _Cap(logging.Handler):
        def emit(self, record):
            captured.append(record)

    audit_logger = logging.getLogger("audit")
    h = _Cap()
    audit_logger.addHandler(h)
    audit_logger.setLevel(logging.INFO)
    try:
        await log(session, tenant_id="t1", user_id="u1", action="probe",
                  details={"hello": "world"})
        await session.commit()
    finally:
        audit_logger.removeHandler(h)
    assert captured, "expected an `audit` logger record"
    rec = captured[-1]
    assert hasattr(rec, "audit")
    assert rec.audit["action"] == "probe"
    assert rec.audit["entry_hash"]
    assert rec.audit["seq"] == 1


def test_json_formatter_emits_request_id(monkeypatch):
    """Logger output is parseable JSON with request id when context is set."""
    import json
    from app.logging_config import JsonFormatter
    from app.middleware.request_context import RequestContextInfo, _ctx

    fmt = JsonFormatter()
    rec = logging.LogRecord("x", logging.INFO, __file__, 1, "hello", None, None)
    token = _ctx.set(RequestContextInfo(request_id="rid-123", ip="1.2.3.4", user_agent="UA"))
    try:
        out = fmt.format(rec)
    finally:
        _ctx.reset(token)
    parsed = json.loads(out)
    assert parsed["msg"] == "hello"
    assert parsed["level"] == "INFO"
    assert parsed["request_id"] == "rid-123"
