"""Phase 0 + Phase 1 (H-8) regression tests.

Covers:
- ``log_security_event`` helper creates rows, does not auto-commit, swallows
  errors instead of breaking callers.
- ``GET /admin/me`` for a bootstrap-listed user who is already an ADMIN emits
  exactly one ``bootstrap_admin_check`` audit row, throttled to once per 24h.
"""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy import select

from app.api.endpoints.admin import get_admin_status
from app.core.logging import log_security_event, extract_audit_context
from app.models.models import AuditLog, User, UserRole
from app.schemas.auth import UserInfo


# ── Phase 0 helper ──────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_log_security_event_creates_row(db):
    user = User(
        id=str(uuid.uuid4()),
        azure_id="oid-1",
        email="phase0@test.com",
        name="Phase 0",
        role=UserRole.USER,
    )
    db.add(user)
    await db.flush()

    row = await log_security_event(
        db,
        user_id=user.id,
        action="unit_test_event",
        event_type="test",
        resource_type="user",
        resource_id=user.id,
        details={"k": "v"},
        ip_address="10.0.0.1",
        user_agent="pytest/1.0",
    )

    assert row is not None
    assert row.id is not None
    assert row.action == "unit_test_event"
    assert row.details == {"k": "v"}
    assert row.ip_address == "10.0.0.1"

    # Helper must NOT auto-commit — caller owns the transaction.
    # Rolling back should discard the row.
    await db.rollback()
    after = await db.execute(
        select(AuditLog).where(AuditLog.action == "unit_test_event")
    )
    assert after.scalar_one_or_none() is None


@pytest.mark.asyncio
async def test_log_security_event_failure_returns_none(db):
    # Pass an invalid user_id (NOT NULL violation via missing FK target).
    # Helper must swallow and return None instead of raising.
    result = await log_security_event(
        db,
        user_id=None,  # type: ignore[arg-type]
        action="should_fail",
        resource_type="user",
    )
    assert result is None


def test_extract_audit_context_handles_none():
    ctx = extract_audit_context(None)
    assert ctx == {"ip_address": None, "user_agent": None}


def test_extract_audit_context_prefers_xff():
    class _FakeReq:
        class client:
            host = "10.0.0.1"
        headers = {"x-forwarded-for": "1.2.3.4, 10.0.0.1", "user-agent": "ua/1"}

    ctx = extract_audit_context(_FakeReq())
    assert ctx["ip_address"] == "1.2.3.4"
    assert ctx["user_agent"] == "ua/1"


# ── Phase 1: H-8 — bootstrap already-admin audit row ────────────────────────


def _bootstrap_user(email: str, oid: str) -> UserInfo:
    return UserInfo(
        id=oid,
        email=email,
        name="Bootstrap",
        roles=[],  # no Entra Admin role — proves DB role is what counts
        tenant_id=None,
    )


@pytest.mark.asyncio
async def test_h8_bootstrap_already_admin_emits_audit_row(db, monkeypatch):
    """A bootstrap-listed user who is already ADMIN must produce an audit row
    on the FIRST call within a 24h window."""
    from app.core.config import settings

    email = "boot-admin@test.com"
    oid = "boot-oid-1"

    monkeypatch.setattr(
        settings, "BOOTSTRAP_ADMIN_EMAILS", email, raising=False
    )
    # Also patch the cached list helpers.
    monkeypatch.setattr(
        type(settings),
        "bootstrap_admin_email_list",
        property(lambda self: [email.lower()]),
    )
    monkeypatch.setattr(
        type(settings),
        "bootstrap_admin_oid_list",
        property(lambda self: []),
    )

    user = User(
        id=str(uuid.uuid4()),
        azure_id=oid,
        email=email,
        name="Boot Admin",
        role=UserRole.ADMIN,  # already an admin
    )
    db.add(user)
    await db.commit()

    result = await get_admin_status(
        current_user=_bootstrap_user(email, oid), db=db
    )
    assert result["is_admin"] is True

    rows = (
        await db.execute(
            select(AuditLog).where(
                AuditLog.user_id == user.id,
                AuditLog.action == "bootstrap_admin_check",
            )
        )
    ).scalars().all()
    assert len(rows) == 1
    assert rows[0].details.get("already_admin") is True


@pytest.mark.asyncio
async def test_h8_bootstrap_check_is_throttled_within_24h(db, monkeypatch):
    """Three back-to-back calls produce exactly one audit row."""
    from app.core.config import settings

    email = "boot-admin-2@test.com"
    oid = "boot-oid-2"

    monkeypatch.setattr(
        type(settings),
        "bootstrap_admin_email_list",
        property(lambda self: [email.lower()]),
    )
    monkeypatch.setattr(
        type(settings),
        "bootstrap_admin_oid_list",
        property(lambda self: []),
    )

    user = User(
        id=str(uuid.uuid4()),
        azure_id=oid,
        email=email,
        name="Boot Admin 2",
        role=UserRole.ADMIN,
    )
    db.add(user)
    await db.commit()

    for _ in range(3):
        await get_admin_status(
            current_user=_bootstrap_user(email, oid), db=db
        )

    rows = (
        await db.execute(
            select(AuditLog).where(
                AuditLog.user_id == user.id,
                AuditLog.action == "bootstrap_admin_check",
            )
        )
    ).scalars().all()
    assert len(rows) == 1
