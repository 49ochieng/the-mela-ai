"""Tests for the GDPR account-deletion worker."""
from __future__ import annotations

from datetime import datetime, timedelta

import pytest
from sqlalchemy import select

from app.models import (
    AgentToken,
    GraphConnection,
    ScanRun,
    ScanSettings,
    Session as SessionModel,
    Tenant,
    User,
)
from app.workers import account_deleter


async def _seed_user(session, *, deletion_at: datetime | None) -> tuple[str, str]:
    tenant = Tenant(entra_tenant_id=f"t-{deletion_at}", name="acme")
    session.add(tenant)
    await session.flush()
    user = User(
        tenant_id=tenant.id,
        entra_user_id=f"u-{deletion_at}",
        display_name="Alice",
        email=f"alice-{deletion_at}@example.com",
        deletion_requested_at=deletion_at,
    )
    session.add(user)
    await session.flush()
    # Per-user rows that should be wiped.
    session.add_all([
        ScanSettings(tenant_id=tenant.id, user_id=user.id),
        ScanRun(tenant_id=tenant.id, user_id=user.id, scan_type="all",
                source_scope={}, status="completed"),
        GraphConnection(tenant_id=tenant.id, user_id=user.id, status="connected"),
        AgentToken(tenant_id=tenant.id, user_id=user.id, name="t",
                   token_hash="h" + str(hash((tenant.id, user.id)))[-30:]),
        SessionModel(tenant_id=tenant.id, user_id=user.id,
                     jti=f"jti-{user.id}", issued_at=datetime.utcnow(),
                     last_seen_at=datetime.utcnow(),
                     expires_at=datetime.utcnow() + timedelta(days=1)),
    ])
    await session.commit()
    return tenant.id, user.id


@pytest.mark.asyncio
async def test_account_deleter_skips_users_within_grace(session, monkeypatch):
    # Stamped 5 days ago, grace is 30 → must NOT delete.
    tid, uid = await _seed_user(session, deletion_at=datetime.utcnow() - timedelta(days=5))

    monkeypatch.setattr(account_deleter, "session_scope",
                        lambda: _DummyScope(session))
    n = await account_deleter.run_due_deletions(grace_days=30)
    assert n == 0

    user = (await session.execute(select(User).where(User.id == uid))).scalar_one()
    assert user is not None
    assert user.deletion_requested_at is not None


@pytest.mark.asyncio
async def test_account_deleter_hard_deletes_after_grace(session, monkeypatch):
    tid, uid = await _seed_user(session, deletion_at=datetime.utcnow() - timedelta(days=45))

    monkeypatch.setattr(account_deleter, "session_scope",
                        lambda: _DummyScope(session))
    n = await account_deleter.run_due_deletions(grace_days=30)
    assert n == 1

    # User and per-user rows are gone.
    assert (await session.execute(select(User).where(User.id == uid))).scalar_one_or_none() is None
    for model in (ScanSettings, ScanRun, GraphConnection, AgentToken, SessionModel):
        rows = (await session.execute(select(model).where(model.user_id == uid))).scalars().all()
        assert rows == [], f"{model.__name__} rows leaked for deleted user"


@pytest.mark.asyncio
async def test_account_deleter_ignores_unrequested_users(session, monkeypatch):
    tid, uid = await _seed_user(session, deletion_at=None)

    monkeypatch.setattr(account_deleter, "session_scope",
                        lambda: _DummyScope(session))
    n = await account_deleter.run_due_deletions(grace_days=30)
    assert n == 0
    user = (await session.execute(select(User).where(User.id == uid))).scalar_one()
    assert user is not None


class _DummyScope:
    """Async context manager that yields the test's existing session."""

    def __init__(self, session):
        self.session = session

    async def __aenter__(self):
        return self.session

    async def __aexit__(self, exc_type, exc, tb):
        return False
