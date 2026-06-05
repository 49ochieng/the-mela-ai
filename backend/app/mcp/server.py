"""
Mela AI - MCP-over-HTTP server (Phase 6A).

Single ``POST /`` dispatcher keyed on the ``tool`` field of the JSON
body, mirroring the wire shape Mela's adapter sends to Task Radar.
Six tools — see ``app/mcp/tools.py``.

Per-tool handlers stay thin: they translate the inbound MCP arguments
into the existing service-layer calls (chat_service, knowledge_store,
executor, …).  The MCP server is a CALLER — it doesn't introduce new
business logic, just a new entry point.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone
from typing import Any, Optional

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.mcp.auth import assert_tool_scope, require_mcp_client
from app.mcp.tools import MELA_TOOL_DEFS
from app.models.models import (
    MCPClient,
    OrchestrationTask,
    OrchestrationTrace,
)
from app.orchestration.executor import executor
from app.orchestration.health import get_worker_health_summary
from app.orchestration.knowledge import KBEntry, knowledge_store
from app.orchestration.types import Priority

logger = logging.getLogger(__name__)
router = APIRouter()


class MCPCall(BaseModel):
    model_config = ConfigDict(extra="ignore")
    tool: str
    arguments: dict[str, Any] = Field(default_factory=dict)


# ── Tool discovery ──────────────────────────────────────────────────────


@router.get("/tools")
async def list_tools(
    _client: MCPClient = Depends(require_mcp_client),
) -> dict[str, Any]:
    """Authenticated tool discovery.

    Returns OpenAI-compatible function definitions filtered to the
    client's scope so callers see exactly what they can invoke.
    """
    from app.mcp.tools import is_tool_in_scope
    visible = [
        t for t in MELA_TOOL_DEFS
        if is_tool_in_scope(t["function"]["name"], _client.scopes or [])
    ]
    return {"tools": visible}


# ── Main dispatcher ─────────────────────────────────────────────────────


@router.post("")
@router.post("/")
async def mcp_dispatch(
    call: MCPCall,
    client: MCPClient = Depends(require_mcp_client),
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    """Single-entry-point MCP dispatcher.  Per-tool handlers below."""
    tool_name = (call.tool or "").strip()
    if not tool_name:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="`tool` is required",
        )
    assert_tool_scope(client, tool_name)
    args = call.arguments or {}

    handler = _HANDLERS.get(tool_name)
    if handler is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"unknown tool: {tool_name!r}",
        )
    return await handler(args=args, client=client, db=db)


# ── Tool handlers ───────────────────────────────────────────────────────


async def _tool_mela_chat(
    *, args: dict[str, Any], client: MCPClient, db: AsyncSession,
) -> dict[str, Any]:
    """Run a chat turn through the standard chat pipeline.

    Mints a synthetic ``UserInfo`` from the MCP client's stated user_id +
    tenant_id.  We don't go through MSAL — the MCP client has already
    been authenticated by its API key; we trust the user_id it claims
    on behalf of within its own tenant.
    """
    message = (args.get("message") or "").strip()
    if not message:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="`message` is required",
        )
    profile_mode = args.get("profile_mode") or "personal"
    if profile_mode not in ("personal", "work"):
        profile_mode = "personal"
    tenant_id = args.get("tenant_id") or client.tenant_id
    user_id = args.get("user_id") or f"mcp:{client.id}"

    # Minimal UserInfo for the pipeline — chat_service reads only
    # id/email/name/roles/groups/tenant_id and ignores anything else.
    from app.schemas.auth import UserInfo
    from app.schemas.chat import ChatRequest

    user_info = UserInfo(
        id=user_id,
        email=f"{user_id}@mcp.mela.local",
        name=client.client_name,
        roles=[],
        groups=[],
        tenant_id=tenant_id or "",
    )
    request = ChatRequest(
        message=message,
        conversation_id=args.get("conversation_id"),
        model="auto",
        stream=False,
        is_private=False,
        context_type="org" if profile_mode == "work" else "personal",
    )
    # Synthesise the optional profile-context attribute the pipeline
    # uses for namespace isolation.
    from app.core.profile_context import ProfileContext
    request._profile_context = ProfileContext(  # type: ignore[attr-defined]
        profile_mode=profile_mode,
        tenant_id=tenant_id if profile_mode == "work" else None,
    )

    # Stream the chat; collect the full response.  MCP is request/
    # response so we don't expose the chunked SSE shape externally —
    # callers that want streaming use the embed iframe instead.
    from app.services.outcome_orchestrator import outcome_orchestrator

    response_content_parts: list[str] = []
    conversation_id: Optional[str] = None
    citations: list[dict[str, Any]] = []
    try:
        async for chunk in outcome_orchestrator.run(db, user_info, request):
            if chunk.type == "content" and chunk.content:
                response_content_parts.append(chunk.content)
            elif chunk.type == "citation" and chunk.data:
                citations.append(chunk.data)
            elif chunk.type == "done" and chunk.data:
                conversation_id = (
                    chunk.data.get("conversation_id") or conversation_id
                )
    except Exception as exc:  # noqa: BLE001 — never raise from MCP
        logger.warning("mela_chat exec failed: %s", exc)
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"chat pipeline failed: {exc}",
        )

    return {
        "response": "".join(response_content_parts),
        "conversation_id": conversation_id,
        "citations": citations,
    }


async def _tool_mela_search_knowledge(
    *, args: dict[str, Any], client: MCPClient, db: AsyncSession,
) -> dict[str, Any]:
    query = (args.get("query") or "").strip()
    tenant_id = args.get("tenant_id")
    user_id = args.get("user_id")
    if not query or not tenant_id or not user_id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="query, tenant_id, and user_id are required",
        )
    entry_types = args.get("entry_types")
    if entry_types is not None and not isinstance(entry_types, list):
        entry_types = None
    limit = int(args.get("limit") or 5)
    limit = max(1, min(50, limit))

    rows = await knowledge_store.search(
        db,
        tenant_id=tenant_id,
        user_id=user_id,
        query=query,
        limit=limit,
        entry_types=entry_types,
    )
    return {
        "results": [
            {
                "title": r.title,
                "summary": r.summary,
                "source_worker_id": r.source_worker_id,
                "entry_type": r.entry_type,
                "tags": list(r.tags or []),
                "data_pointer": r.data_pointer,
                "created_at": (
                    r.created_at.isoformat() if r.created_at else None
                ),
            }
            for r in rows
        ],
    }


async def _tool_mela_get_worker_status(
    *, args: dict[str, Any], client: MCPClient, db: AsyncSession,
) -> dict[str, Any]:
    summary = await get_worker_health_summary(db)
    worker_id = args.get("worker_id")
    if worker_id:
        summary["workers"] = [
            w for w in summary["workers"] if w["id"] == worker_id
        ]
        summary["worker_count"] = len(summary["workers"])
    return summary


# 30-second cap for sync mela_trigger_plan calls.  Anything longer
# than this is pathological — admins should use background mode.
_SYNC_PLAN_TIMEOUT = 30.0


async def _tool_mela_trigger_plan(
    *, args: dict[str, Any], client: MCPClient, db: AsyncSession,
) -> dict[str, Any]:
    goal = (args.get("goal") or "").strip()
    user_id = args.get("user_id")
    tenant_id = args.get("tenant_id")
    if not goal or not user_id or not tenant_id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="goal, user_id, and tenant_id are required",
        )
    mode = args.get("execution_mode") or "background"
    if mode not in ("sync", "background"):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="execution_mode must be 'sync' or 'background'",
        )

    from app.services.orchestration_planner import (
        AnnotatedPlan,
        PlanningContext,
        PlanningFailure,
        orchestration_planner,
    )

    ctx = PlanningContext(
        user_id=str(user_id),
        tenant_id=str(tenant_id),
        profile_mode="work",
        priority=Priority.NORMAL,
    )
    outcome = await orchestration_planner.plan(goal, ctx, db)
    if isinstance(outcome, PlanningFailure):
        return {
            "trace_id": None,
            "status": "planning_failure",
            "reason": outcome.reason,
            "detail": outcome.detail,
            "estimated_total_ms": 0,
        }

    plan: AnnotatedPlan = outcome
    trace_id = next(
        (t.trace_id for batch in plan.plan.batches for t in batch.tasks),
        None,
    )

    if mode == "background":
        async def _bg() -> None:
            try:
                from app.core.database import async_session_maker
                async with async_session_maker() as bg_db:
                    await executor.run_plan(bg_db, plan.plan)
            except Exception as exc:  # noqa: BLE001
                logger.warning(
                    "mela_trigger_plan background run failed trace=%s err=%s",
                    trace_id, exc,
                )

        asyncio.create_task(_bg())
        return {
            "trace_id": trace_id,
            "status": "queued",
            "estimated_total_ms": plan.estimated_total_ms,
            "slow_plan": plan.slow_plan,
        }

    # Sync mode — execute and bound the wait.  ``run_plan`` already
    # uses asyncio.gather on each batch; we wrap the whole thing in
    # ``wait_for`` so a slow plan can't hold the MCP request open
    # past the 30-second cap.
    try:
        execution = await asyncio.wait_for(
            executor.run_plan(db, plan.plan),
            timeout=_SYNC_PLAN_TIMEOUT,
        )
    except asyncio.TimeoutError:
        return {
            "trace_id": trace_id,
            "status": "partial",
            "reason": "sync timeout — plan still running in background",
            "estimated_total_ms": plan.estimated_total_ms,
        }
    return {
        "trace_id": execution.trace_id,
        "status": (
            "completed" if execution.success
            else "partial" if execution.partial
            else "failed"
        ),
        "estimated_total_ms": plan.estimated_total_ms,
        "results": [
            {
                "worker_id": r.worker_id,
                "capability": r.capability,
                "success": r.success,
                "summary": r.summary,
            }
            for r in execution.results
        ],
    }


async def _tool_mela_get_trace_status(
    *, args: dict[str, Any], client: MCPClient, db: AsyncSession,
) -> dict[str, Any]:
    trace_id = args.get("trace_id")
    if not trace_id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="trace_id is required",
        )
    trace = await db.get(OrchestrationTrace, trace_id)
    if trace is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"trace {trace_id!r} not found",
        )
    rows = (
        await db.execute(
            select(OrchestrationTask)
            .where(OrchestrationTask.trace_id == trace_id)
            .order_by(OrchestrationTask.created_at.asc())
        )
    ).scalars().all()
    return {
        "trace_id": trace.trace_id,
        "status": trace.status,
        "created_at": (
            trace.created_at.isoformat() if trace.created_at else None
        ),
        "completed_at": (
            trace.completed_at.isoformat() if trace.completed_at else None
        ),
        "tasks": [
            {
                "task_id": t.task_id,
                "worker_id": t.worker_id,
                "capability": t.capability,
                "status": t.status,
                "summary": t.summary,
                "latency_ms": t.latency_ms,
                "error_code": t.error_code,
            }
            for t in rows
        ],
    }


async def _tool_mela_ingest_context(
    *, args: dict[str, Any], client: MCPClient, db: AsyncSession,
) -> dict[str, Any]:
    title = (args.get("title") or "").strip()
    summary = (args.get("summary") or "").strip()
    entry_type = (args.get("entry_type") or "").strip()
    tenant_id = args.get("tenant_id")
    user_id = args.get("user_id")
    if not (title and summary and entry_type and tenant_id and user_id):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=(
                "title, summary, entry_type, tenant_id, and user_id "
                "are required"
            ),
        )
    tags = args.get("tags") or []
    if not isinstance(tags, list):
        tags = []

    # Source attribution: the calling MCP client's ``client_name`` —
    # NOT anything the caller echoed.  This keeps the audit trail
    # honest about who pushed the entry.
    entry = KBEntry(
        user_id=str(user_id),
        tenant_id=str(tenant_id),
        profile_mode="work",
        source_worker_id=f"mcp:{client.client_name}",
        entry_type=entry_type,
        title=title,
        summary=summary,
        data_pointer=args.get("data_pointer"),
        tags=list(tags),
    )
    row = await knowledge_store.ingest(db, entry)
    return {
        "entry_id": row.entry_id,
        "source_worker_id": row.source_worker_id,
        "expires_at": (
            row.expires_at.isoformat() if row.expires_at else None
        ),
    }


_HANDLERS = {
    "mela_chat":               _tool_mela_chat,
    "mela_search_knowledge":   _tool_mela_search_knowledge,
    "mela_get_worker_status":  _tool_mela_get_worker_status,
    "mela_trigger_plan":       _tool_mela_trigger_plan,
    "mela_get_trace_status":   _tool_mela_get_trace_status,
    "mela_ingest_context":     _tool_mela_ingest_context,
}


# Quiet ruff for the unused datetime/timezone imports — reserved for
# future tool handlers that need them.
_ = (datetime, timezone)
