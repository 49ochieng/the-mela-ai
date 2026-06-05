"""Phase 2 verification: session lifecycle helpers.

These tests exercise the in-process behaviour of ``app.core.sessions``
against an in-memory SQLite database. They do NOT spin up the full
FastAPI app — that path is covered by the existing integration tests.
"""

from __future__ import annotations

import asyncio
import os
import uuid
from datetime import datetime, timedelta

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import sessionmaker

# Force SQLite + a unique secret so the import-time settings validation passes.
os.environ.setdefault("APP_ENV", "development")
os.environ.setdefault("DEBUG", "true")
os.environ.setdefault("USE_SQLITE", "true")
os.environ.setdefault("JWT_SECRET_KEY", "test-key-" + "x" * 24)


@pytest.fixture()
async def db_session():
    from app.models.models import Base, User, UserSession  # noqa: F401

    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    Session = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with Session() as session:
        # Insert a parent user so the FK constraint holds.
        user = User(
            id=str(uuid.uuid4()),
            azure_id="azure-test-1",
            email="t@example.com",
            name="Tester",
            is_active=True,
        )
        session.add(user)
        await session.commit()
        yield session, user.id
    await engine.dispose()


def test_derive_jti_prefers_claim():
    from app.core.sessions import derive_jti

    assert derive_jti("any-token", "abc-123") == "abc-123"
    assert derive_jti("token-x").startswith("sha256:")


@pytest.mark.asyncio
async def test_session_lifecycle(db_session):
    from app.core.sessions import (
        get_or_create_session,
        revoke_session_by_jti,
        revoke_all_user_sessions,
        session_is_valid,
    )

    db, user_id = db_session
    jti = "jti-1"

    sess = await get_or_create_session(db, user_id=user_id, token_jti=jti)
    assert sess is not None
    ok, reason = session_is_valid(sess)
    assert ok and reason == ""

    # Idempotent: same jti returns same row
    sess2 = await get_or_create_session(db, user_id=user_id, token_jti=jti)
    assert sess2.id == sess.id

    # Force idle timeout
    sess.last_activity_at = datetime.utcnow() - timedelta(hours=1)
    ok, reason = session_is_valid(sess)
    assert not ok and reason == "session_idle_timeout"

    # Revoke single session
    revoked = await revoke_session_by_jti(db, jti)
    assert revoked == 1
    await db.refresh(sess)
    ok, reason = session_is_valid(sess)
    assert not ok and reason == "session_revoked"

    # Revoke-all on a fresh session
    sess3 = await get_or_create_session(db, user_id=user_id, token_jti="jti-2")
    n = await revoke_all_user_sessions(db, user_id)
    assert n >= 1
    await db.refresh(sess3)
    ok, reason = session_is_valid(sess3)
    assert not ok and reason == "session_revoked"


def test_log_redaction_filter():
    from app.core.logging import _SecretRedactFilter
    import logging

    f = _SecretRedactFilter()
    rec = logging.LogRecord(
        name="x", level=logging.INFO, pathname="", lineno=1,
        msg="auth header Bearer eyJabc.def.ghi and api_key=deadbeefdeadbeef",
        args=(), exc_info=None,
    )
    assert f.filter(rec) is True
    assert "Bearer" not in rec.msg or "[REDACTED]" in rec.msg
    assert "deadbeefdeadbeef" not in rec.msg
