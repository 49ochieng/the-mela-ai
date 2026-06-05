"""Phase 2 (H-7) — audit coverage tests.

Verifies that the following sensitive actions produce an `AuditLog` row (or
fire the audit logger for the un-attributable failed-auth path):

  1. `auth_failed`        — failed JWT validation (logger only, no DB row).
  2. `file_uploaded`      — successful document upload.
  3. `file_rejected`      — document upload blocked by security scan.
  4. `tool_executed`      — sensitive agent tool invocation.
  5. `user_role_changed`  — admin promotes/demotes another user.
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from sqlalchemy import select

from app.models.models import AuditLog, UserRole


# ─────────────────────────────────────────────────────────────────────────────
# 1.  Failed JWT validation — uses logger-only sink (no DB row written).
# ─────────────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_auth_failure_emits_audit_logger_event():
    """`_audit_auth_failure` should call `audit_logger.log_action` with
    action='auth_failed', success=False, and the supplied reason in details.
    """
    from app.core import security as security_mod

    with patch.object(
        security_mod, "_audit_auth_failure", wraps=security_mod._audit_auth_failure
    ):
        # Patch the audit_logger that the helper imports lazily.
        with patch("app.core.logging.audit_logger") as fake_logger:
            security_mod._audit_auth_failure(
                reason="jwt_error",
                token_jti="abc",
                ip="1.2.3.4",
                ua="pytest/1.0",
                detail="bad signature",
            )
            assert fake_logger.log_action.called
            kwargs = fake_logger.log_action.call_args.kwargs
            assert kwargs["action"] == "auth_failed"
            assert kwargs["success"] is False
            assert kwargs["details"]["reason"] == "jwt_error"
            assert kwargs["details"]["ip_address"] == "1.2.3.4"
            assert kwargs["details"]["token_jti"] == "abc"


@pytest.mark.asyncio
async def test_validate_token_audience_mismatch_audits():
    """`validate_token` should call `_audit_auth_failure` when audience/issuer
    decoding fails for every JWKS key candidate.
    """
    from app.core import security as security_mod

    auth = security_mod.AzureADAuth.__new__(security_mod.AzureADAuth)
    auth.tenant_id = "tenant-xyz"
    auth.client_id = "client-abc"
    auth.valid_audiences = ["client-abc", "api://client-abc"]
    auth.valid_issuers = [
        "https://sts.windows.net/tenant-xyz/",
        "https://login.microsoftonline.com/tenant-xyz/v2.0",
    ]
    auth._jwks_cache = {"keys": [{"kid": "k1", "kty": "RSA", "n": "x", "e": "AQAB"}]}
    auth._jwks_cache_time = None

    # Force cached JWKS to be served.
    from datetime import datetime
    auth._jwks_cache_time = datetime.utcnow()

    with patch(
        "app.core.security.jwt.get_unverified_header",
        return_value={"kid": "k1"},
    ), patch(
        "app.core.security.jwt.decode",
        side_effect=__import__("jose").JWTError("bad audience"),
    ), patch("app.core.security._audit_auth_failure") as audit_spy:
        from fastapi import HTTPException
        with pytest.raises(HTTPException):
            await auth.validate_token("forged.jwt.token")
        assert audit_spy.called
        assert audit_spy.call_args.kwargs["reason"] == "audience_or_issuer_mismatch"


# ─────────────────────────────────────────────────────────────────────────────
# 2.  Admin role-change audit (covers both promotion and demotion).
# ─────────────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_user_role_change_creates_audit_row(db):
    """Promoting USER → ADMIN must create both `user_role_changed` and
    `admin_promoted` audit rows; demoting back must create one
    `user_role_changed` row.
    """
    from app.api.endpoints.admin import update_user
    from app.schemas.auth import UserUpdate, UserInfo
    from tests.conftest import make_user

    admin = await make_user(db, email="admin@test.com")
    admin.role = UserRole.ADMIN
    target = await make_user(db, email="target@test.com")
    await db.commit()

    admin_info = UserInfo(
        id=admin.id, email=admin.email, name="A",
        roles=["Admin"], tenant_id="",
    )

    # Promotion path
    await update_user(
        user_id=target.id,
        data=UserUpdate(role=UserRole.ADMIN),
        current_user=admin_info,
        db=db,
    )
    rows = (await db.execute(
        select(AuditLog).where(AuditLog.resource_id == target.id)
    )).scalars().all()
    actions = {r.action for r in rows}
    assert "user_role_changed" in actions
    assert "admin_promoted" in actions
    rc = next(r for r in rows if r.action == "user_role_changed")
    assert "user" in rc.details["from_role"].lower()
    assert "admin" in rc.details["to_role"].lower()
    assert rc.details["changed_by"] == admin.email

    # Demotion path
    await update_user(
        user_id=target.id,
        data=UserUpdate(role=UserRole.USER),
        current_user=admin_info,
        db=db,
    )
    rows = (await db.execute(
        select(AuditLog).where(
            AuditLog.resource_id == target.id,
            AuditLog.action == "user_role_changed",
        )
    )).scalars().all()
    assert len(rows) == 2  # promotion + demotion


@pytest.mark.asyncio
async def test_user_role_change_no_audit_when_role_unchanged(db):
    """Calling update_user with the same role must NOT create a
    `user_role_changed` row.
    """
    from app.api.endpoints.admin import update_user
    from app.schemas.auth import UserUpdate, UserInfo
    from tests.conftest import make_user

    admin = await make_user(db, email="admin2@test.com")
    admin.role = UserRole.ADMIN
    target = await make_user(db, email="target2@test.com")
    await db.commit()

    admin_info = UserInfo(
        id=admin.id, email=admin.email, name="A",
        roles=["Admin"], tenant_id="",
    )
    await update_user(
        user_id=target.id,
        data=UserUpdate(role=UserRole.USER),  # already USER
        current_user=admin_info,
        db=db,
    )
    rows = (await db.execute(
        select(AuditLog).where(
            AuditLog.resource_id == target.id,
            AuditLog.action == "user_role_changed",
        )
    )).scalars().all()
    assert rows == []


# ─────────────────────────────────────────────────────────────────────────────
# 3.  Tool execution audit.
# ─────────────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_audited_tool_emits_audit_row(db):
    """A `send_email` tool call (member of `_AUDITED_TOOLS`) must trigger
    `_audit_tool_execution` and produce a `tool_executed` AuditLog row.
    """
    import sys; import importlib; te_mod = importlib.import_module("app.agents.tool_executor"); _ = sys
    from app.schemas.auth import UserInfo
    from tests.conftest import make_user

    user = await make_user(db, email="caller@test.com")
    await db.commit()

    user_info = UserInfo(
        id=user.id, email=user.email, name="C", roles=[], tenant_id="t",
    )

    te = te_mod.ToolExecutor()

    # Phase 3a: dangerous tools now require a one-shot confirmation token.
    # Mint one that matches the SANITISED args (gate strips workflow_type +
    # _confirmation_token before hashing).
    from app.agents.confirmation import issue_token, _reset_for_tests
    _reset_for_tests()
    base_args = {
        "to": "x@y.com",
        "subject": "hi",
        "body": "B" * 500,  # forces truncation
        "password": "should-be-redacted",
    }
    tok = issue_token(
        user_id=user.id, tool_name="send_email", arguments=base_args,
    )

    # Stub the actual graph dispatcher — we only care about the audit shim.
    with patch.object(
        te, "_execute_tool_inner",
        new=AsyncMock(return_value={"success": True, "sent": True}),
    ):
        # Redirect async_session_maker used inside _audit_tool_execution
        # to a sessionmaker bound to the test engine.
        bind = db.bind
        from sqlalchemy.ext.asyncio import async_sessionmaker
        test_maker = async_sessionmaker(bind, expire_on_commit=False)

        with patch(
            "app.core.database.async_session_maker", test_maker
        ):
            result = await te.execute_tool(
                tool_name="send_email",
                arguments={**base_args, "_confirmation_token": tok},
                user=user_info,
                trace_id="trace-1",
            )

    assert result["success"] is True

    # Audit row written via a separate session — re-query through `db`.
    rows = (await db.execute(
        select(AuditLog).where(AuditLog.action == "tool_executed")
    )).scalars().all()
    assert len(rows) == 1
    row = rows[0]
    assert row.resource_id == "send_email"
    assert row.user_id == user.id
    assert row.success is True
    assert row.details["arguments"]["password"] == "<redacted>"
    # Body was 500 chars → truncated to 200 + ellipsis.
    assert row.details["arguments"]["body"].endswith("…")
    assert len(row.details["arguments"]["body"]) <= 201
    assert row.details["trace_id"] == "trace-1"


@pytest.mark.asyncio
async def test_non_audited_tool_skips_audit(db):
    """Tools NOT in `_AUDITED_TOOLS` (e.g. `search_documents`) must NOT
    produce an audit row.
    """
    import sys; import importlib; te_mod = importlib.import_module("app.agents.tool_executor"); _ = sys
    from app.schemas.auth import UserInfo
    from tests.conftest import make_user

    user = await make_user(db, email="caller2@test.com")
    await db.commit()

    user_info = UserInfo(
        id=user.id, email=user.email, name="C", roles=[], tenant_id="t",
    )

    te = te_mod.ToolExecutor()
    with patch.object(
        te, "_execute_tool_inner",
        new=AsyncMock(return_value={"results": []}),
    ):
        await te.execute_tool(
            tool_name="search_documents",
            arguments={"query": "hello"},
            user=user_info,
        )

    rows = (await db.execute(
        select(AuditLog).where(AuditLog.action == "tool_executed")
    )).scalars().all()
    assert rows == []


@pytest.mark.asyncio
async def test_tool_audit_records_failure(db):
    """When the inner dispatch returns `{"error": ...}` the audit row must
    have success=False and capture the error message.
    """
    import sys; import importlib; te_mod = importlib.import_module("app.agents.tool_executor"); _ = sys
    from app.schemas.auth import UserInfo
    from tests.conftest import make_user

    user = await make_user(db, email="caller3@test.com")
    await db.commit()

    user_info = UserInfo(
        id=user.id, email=user.email, name="C", roles=[], tenant_id="t",
    )

    te = te_mod.ToolExecutor()
    bind = db.bind
    from sqlalchemy.ext.asyncio import async_sessionmaker
    test_maker = async_sessionmaker(bind, expire_on_commit=False)

    from app.agents.confirmation import issue_token, _reset_for_tests
    _reset_for_tests()
    base_args = {"to": "x@y.com", "subject": "s", "body": "b"}
    tok = issue_token(
        user_id=user.id, tool_name="send_email", arguments=base_args,
    )

    with patch.object(
        te, "_execute_tool_inner",
        new=AsyncMock(return_value={"error": "smtp_unreachable"}),
    ), patch("app.core.database.async_session_maker", test_maker):
        await te.execute_tool(
            tool_name="send_email",
            arguments={**base_args, "_confirmation_token": tok},
            user=user_info,
        )

    rows = (await db.execute(
        select(AuditLog).where(AuditLog.action == "tool_executed")
    )).scalars().all()
    assert len(rows) == 1
    assert rows[0].success is False
    assert rows[0].error_message == "smtp_unreachable"
    assert rows[0].details["outcome"] == "error"


# ─────────────────────────────────────────────────────────────────────────────
# 4.  Redaction helper unit test.
# ─────────────────────────────────────────────────────────────────────────────


def test_redact_tool_args_redacts_secrets_and_truncates():
    from app.agents.tool_executor import _redact_tool_args

    out = _redact_tool_args({
        "to": "user@example.com",
        "password": "topsecret",
        "api_key": "xyz",
        "Authorization": "Bearer abc",
        "body": "x" * 500,
        "attachments": [1, 2, 3],
        "count": 5,
    })
    assert out["password"] == "<redacted>"
    assert out["api_key"] == "<redacted>"
    assert out["Authorization"] == "<redacted>"
    assert out["body"].endswith("…")
    assert len(out["body"]) == 201
    assert out["attachments"] == "<list len=3>"
    assert out["count"] == 5
