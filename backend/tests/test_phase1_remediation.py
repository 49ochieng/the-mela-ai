"""
Tests for Phase 1 Remediation:
1. Session memory wiring in chat finalization
2. Connector sync single-execution (no double-run)
3. Profile context enforcement on get_conversation_detail
"""

import json
import uuid

import pytest
import pytest_asyncio
from unittest.mock import AsyncMock, MagicMock, patch

from app.core.profile_context import ProfileContext
from app.models.models import SessionMemory, MemoryType
from app.services.memory_service import MemoryService
from tests.conftest import make_user, make_conversation


# ═══════════════════════════════════════════════════════════════════════════════
# 1. Session Memory – update_session_memory() integration
# ═══════════════════════════════════════════════════════════════════════════════


@pytest.mark.asyncio
async def test_session_memory_created_on_first_update(db):
    """update_session_memory should create a new SessionMemory row."""
    svc = MemoryService()
    user = await make_user(db)
    conv = await make_conversation(db, user)

    result = await svc.update_session_memory(
        db=db,
        conversation_id=conv.id,
        user_id=user.id,
        summary="User asked about weather. Assistant provided forecast.",
        key_facts=["User is in Chicago"],
        message_count=2,
        profile_mode="personal",
    )

    assert result is not None
    assert result.conversation_id == conv.id
    assert "weather" in result.summary
    assert result.message_count == 2


@pytest.mark.asyncio
async def test_session_memory_updated_on_subsequent_calls(db):
    """Calling update_session_memory twice should update the existing row."""
    svc = MemoryService()
    user = await make_user(db)
    conv = await make_conversation(db, user)

    first = await svc.update_session_memory(
        db=db,
        conversation_id=conv.id,
        user_id=user.id,
        summary="First summary",
        message_count=2,
    )

    second = await svc.update_session_memory(
        db=db,
        conversation_id=conv.id,
        user_id=user.id,
        summary="Updated summary with more context",
        message_count=4,
    )

    # Same row should be updated, not duplicated
    assert second.conversation_id == first.conversation_id
    assert second.summary == "Updated summary with more context"
    assert second.message_count == 4

    # Verify only one row exists via get
    fetched = await svc.get_session_memory(db, conv.id)
    assert fetched is not None
    assert fetched.summary == "Updated summary with more context"


@pytest.mark.asyncio
async def test_session_memory_injected_into_build_memory_context(db):
    """build_memory_context should include session memory when available."""
    svc = MemoryService()
    user = await make_user(db)
    conv = await make_conversation(db, user)

    await svc.update_session_memory(
        db=db,
        conversation_id=conv.id,
        user_id=user.id,
        summary="Discussed quarterly report metrics.",
        key_facts=["Q3 revenue was $10M"],
        message_count=4,
    )

    context = await svc.build_memory_context(
        db=db,
        user_id=user.id,
        conversation_id=conv.id,
    )

    assert "[SESSION_MEMORY]" in context
    assert "quarterly report" in context


# ═══════════════════════════════════════════════════════════════════════════════
# 2. Connector Sync – no double execution
# ═══════════════════════════════════════════════════════════════════════════════


def test_trigger_sync_does_not_call_run_job_directly():
    """
    Verify that the trigger_sync endpoint only enqueues jobs and does NOT
    call run_job() via BackgroundTasks. The ingestion worker's process_queue()
    loop handles execution.
    """
    import ast
    import inspect
    from app.api.endpoints import connectors

    source = inspect.getsource(connectors.trigger_sync)
    # Parse AST to check for actual code calls, ignoring comments
    tree = ast.parse(source)
    call_names = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Attribute):
            call_names.append(node.attr)
    assert "run_job" not in call_names, (
        "trigger_sync should not call run_job() directly — "
        "jobs are executed by the queue drainer"
    )
    # Verify BackgroundTasks is not a parameter
    sig = inspect.signature(connectors.trigger_sync)
    assert "background_tasks" not in sig.parameters, (
        "trigger_sync should not accept BackgroundTasks — "
        "the ingestion worker's process_queue() loop handles execution"
    )


def test_reindex_sharepoint_does_not_call_run_job_directly():
    """Verify reindex_sharepoint only enqueues, no direct run_job."""
    import inspect
    from app.api.endpoints import connectors

    source = inspect.getsource(connectors.reindex_sharepoint)
    assert "run_job" not in source
    assert "background_tasks" not in source


def test_reindex_org_website_does_not_call_run_job_directly():
    """Verify reindex_org_website only enqueues, no direct run_job."""
    import inspect
    from app.api.endpoints import connectors

    source = inspect.getsource(connectors.reindex_org_website)
    assert "run_job" not in source
    assert "background_tasks" not in source


# ═══════════════════════════════════════════════════════════════════════════════
# 3. Profile Context – get_conversation enforces namespace boundary
# ═══════════════════════════════════════════════════════════════════════════════


@pytest.mark.asyncio
async def test_profile_context_blocks_cross_profile_access(db):
    """
    A personal-mode ProfileContext should reject a work conversation
    when validate_record() is called.
    """
    user = await make_user(db)
    work_conv = await make_conversation(db, user, context_type="org")

    personal_ctx = ProfileContext(profile_mode="personal", tenant_id=None)

    from fastapi import HTTPException
    with pytest.raises(HTTPException) as exc:
        personal_ctx.validate_record(work_conv)
    assert exc.value.status_code == 403


@pytest.mark.asyncio
async def test_profile_context_allows_same_profile_access(db):
    """
    A work-mode ProfileContext should allow access to a work conversation
    with the same tenant.
    """
    user = await make_user(db)
    work_conv = await make_conversation(db, user, context_type="org")

    work_ctx = ProfileContext(profile_mode="work", tenant_id=work_conv.tenant_id)

    # Should NOT raise
    work_ctx.validate_record(work_conv)


@pytest.mark.asyncio
async def test_profile_context_blocks_cross_tenant_access(db):
    """
    A work-mode ProfileContext with tenant A should reject a work
    conversation belonging to tenant B.
    """
    user = await make_user(db)
    work_conv = await make_conversation(db, user, context_type="org")

    # Different tenant
    other_tenant_ctx = ProfileContext(profile_mode="work", tenant_id="other-tenant-999")

    from fastapi import HTTPException
    with pytest.raises(HTTPException) as exc:
        other_tenant_ctx.validate_record(work_conv)
    assert exc.value.status_code == 403


@pytest.mark.asyncio
async def test_get_conversation_endpoint_has_profile_ctx_dependency():
    """
    Verify that the get_conversation endpoint declares ProfileContext
    as a dependency — a structural test to catch accidental removal.
    """
    import inspect
    from app.api.endpoints.chat import get_conversation

    sig = inspect.signature(get_conversation)
    param_names = list(sig.parameters.keys())
    assert "profile_ctx" in param_names, (
        "get_conversation must declare profile_ctx as a FastAPI dependency"
    )
