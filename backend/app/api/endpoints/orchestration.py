"""
Mela AI - Orchestration admin + health endpoints.

Phase 1 surface area:
  GET    /orchestration/registry           → list all registered workers
  GET    /orchestration/registry/{id}      → fetch one worker manifest
  PUT    /orchestration/registry/{id}      → upsert a worker manifest (admin)
  DELETE /orchestration/registry/{id}      → remove a worker (admin)
  GET    /orchestration/health             → cross-worker health summary

Phase 4 additions:
  GET    /orchestration/traces             → admin trace list (paginated)
  GET    /orchestration/traces/{trace_id}  → admin trace detail w/ tasks
  GET    /orchestration/kb/stats           → admin KB summary

Notes
-----
* Mutating endpoints require admin via ``get_current_admin_user``.
* Read endpoints (registry/health) require any authenticated user.
* Trace + KB-stats endpoints are admin-only — they expose
  cross-tenant orchestration data which non-admin users must not see.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any, AsyncGenerator, Optional

from fastapi import APIRouter, Depends, Header, HTTPException, Query, status
from fastapi.responses import StreamingResponse
from sqlalchemy import case, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.database import get_db
from app.core.security import get_current_admin_user, get_current_user
from app.models.models import (
    MCPClient,
    OrchestrationTask,
    OrchestrationTrace,
    WorkerTenantAccess,
)
from app.orchestration.event_bus import event_bus
from app.orchestration.health import get_worker_health_summary
from app.orchestration.knowledge import knowledge_store
from app.orchestration.registry import worker_registry
from app.orchestration.types import WorkerManifest
from app.schemas.auth import UserInfo
from app.schemas.chat import StreamChunk

logger = logging.getLogger(__name__)
router = APIRouter()


@router.get("/registry", response_model=list[WorkerManifest])
async def list_workers(
    _user: UserInfo = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> list[WorkerManifest]:
    return await worker_registry.list(db)


@router.get("/registry/{worker_id}", response_model=WorkerManifest)
async def get_worker(
    worker_id: str,
    _user: UserInfo = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> WorkerManifest:
    manifest = await worker_registry.get(db, worker_id)
    if manifest is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"worker {worker_id!r} not registered",
        )
    return manifest


@router.put("/registry/{worker_id}", response_model=WorkerManifest)
async def upsert_worker(
    worker_id: str,
    manifest: WorkerManifest,
    _admin: UserInfo = Depends(get_current_admin_user),
    db: AsyncSession = Depends(get_db),
) -> WorkerManifest:
    if manifest.id != worker_id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=(
                f"path worker_id={worker_id!r} does not match body id={manifest.id!r}"
            ),
        )
    return await worker_registry.upsert(db, manifest)


@router.delete("/registry/{worker_id}")
async def delete_worker(
    worker_id: str,
    _admin: UserInfo = Depends(get_current_admin_user),
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    removed = await worker_registry.remove(db, worker_id)
    if not removed:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"worker {worker_id!r} not registered",
        )
    return {"removed": worker_id}


# ── Phase 7: code-free worker connection (admin only) ──────────────────


@router.post("/probe")
async def probe_worker(
    body: dict[str, Any],
    _admin: UserInfo = Depends(get_current_admin_user),
) -> dict[str, Any]:
    """Discovery probe — talk to a candidate worker WITHOUT persisting.

    Body: ``{base_url, api_key?, auth_header?, health_path?}``.

    Returns a suggested ``WorkerManifest`` skeleton + discovered
    capability list, or a structured error code the UI can render.
    Never persists.
    """
    from app.orchestration.probe import discover

    base_url = (body or {}).get("base_url")
    api_key = (body or {}).get("api_key")
    auth_header = (body or {}).get("auth_header") or "X-Api-Key"
    health_path = (body or {}).get("health_path") or "/health"

    if not base_url:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="base_url is required",
        )

    result = await discover(
        base_url=base_url,
        api_key=api_key,
        auth_header=auth_header,
        health_path=health_path,
    )

    return {
        "success": result.success,
        "base_url": result.base_url,
        "suggested_id": result.suggested_id,
        "suggested_display_name": result.suggested_display_name,
        "suggested_version": result.suggested_version,
        "suggested_auth_header": auth_header,
        "capabilities": [
            {
                "name": c.name,
                "description": c.description,
                "input_params": c.input_params or {},
                "is_async": c.is_async,
            }
            for c in (result.capabilities or [])
        ],
        "health_ok": result.health_ok,
        "health_latency_ms": result.health_latency_ms,
        "error_code": result.error_code,
        "error_message": result.error_message,
    }


@router.post("/registry/{worker_id}/test")
async def test_worker(
    worker_id: str,
    body: Optional[dict[str, Any]] = None,
    admin: UserInfo = Depends(get_current_admin_user),
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    """Fire one dry MelaTask against a registered worker.

    Body (all optional): ``{capability?, params?}``.  When ``capability``
    is omitted we prefer a declared ``health`` capability, then the first
    sync capability with no required input params.  Returns the
    serialized ``MelaResult``.
    """
    from app.orchestration.executor import executor as orch_executor
    from app.orchestration.types import MelaContext, MelaTask

    manifest = await worker_registry.get(db, worker_id)
    if manifest is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"worker {worker_id!r} not registered",
        )

    body = body or {}
    chosen_cap = body.get("capability")
    if not chosen_cap:
        # Prefer an explicit "health" capability if the worker declared one.
        for cap in manifest.capabilities:
            if cap.name.lower() in ("health", "ping", "status"):
                chosen_cap = cap.name
                break
        if not chosen_cap:
            for cap in manifest.capabilities:
                if not cap.is_async:
                    required = (cap.input_params or {}).get("required") or []
                    if not required:
                        chosen_cap = cap.name
                        break
        if not chosen_cap:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=(
                    "no zero-arg sync capability available for auto-test; "
                    "specify {capability, params}"
                ),
            )

    if not manifest.has_capability(chosen_cap):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"worker {worker_id!r} does not declare {chosen_cap!r}",
        )

    task = MelaTask(
        capability=chosen_cap,
        worker_id=worker_id,
        params=dict(body.get("params") or {}),
        context=MelaContext(
            tenant_id=str(getattr(admin, "tenant_id", None) or "admin-test"),
            user_id=str(admin.id),
        ),
        execution_mode="sync",
    )

    result = await orch_executor.run_single(db, task)
    return {
        "capability": chosen_cap,
        "result": result.model_dump(mode="json"),
    }


@router.get("/health")
async def orchestration_health(
    _user: UserInfo = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    summary = await get_worker_health_summary(db)
    # Phase 5C: surface the policy flag so the admin frontend knows
    # whether to render the Access Control tab as actionable
    # (default-deny mode) or as a "default open" notice.
    summary["access_default_allow"] = bool(
        getattr(settings, "WORKER_ACCESS_DEFAULT_ALLOW", True)
    )
    return summary


# ── Phase 5A: per-user worker-event SSE channel ──────────────────────────


# Heartbeat keeps the connection alive through Azure App Service's
# 230-second idle timeout.  At 30s we have plenty of headroom and each
# heartbeat is ~30 bytes — negligible cost for a kept-open admin
# connection.
_SSE_HEARTBEAT_SECONDS = 30.0


@router.get("/events/stream")
async def orchestration_events_stream(
    user: UserInfo = Depends(get_current_user),
) -> StreamingResponse:
    """Open an SSE channel for this user's live worker events.

    Each connect creates a fresh subscriber queue — multiple browser
    tabs from the same user each get their own stream, none of them
    starve the others.  Disconnect (client close OR server error)
    triggers ``unsubscribe`` so we never leak queues.

    The generator MUST never let an exception bubble out — Starlette's
    middleware stack converts a mid-stream raise into
    ``RuntimeError: No response returned``.  Every error path here
    yields an SSE ``error`` chunk and then returns cleanly.
    """
    user_id = user.id
    tenant_id = getattr(user, "tenant_id", None)

    async def _generator() -> AsyncGenerator[bytes, None]:
        # Emit an immediate "ping" chunk BEFORE any awaits that could
        # fail.  This guarantees the StreamingResponse has at least one
        # byte to flush, so the HTTP response headers are committed
        # even if subscribe() throws.  ``ping`` is a valid StreamChunk
        # type — don't use "connected" or similar (pydantic validation
        # silently kills the generator).
        try:
            hello = StreamChunk(type="ping")
            yield f"data: {hello.model_dump_json()}\n\n".encode("utf-8")
        except Exception as hello_exc:  # noqa: BLE001
            logger.exception(
                "events_stream initial ping failed user=%s err=%r",
                user_id, hello_exc,
            )
            return

        queue = None
        try:
            queue = await event_bus.subscribe(user_id)
        except Exception as exc:  # noqa: BLE001
            logger.error(
                "events_stream subscribe failed user=%s tenant=%s err=%r",
                user_id, tenant_id, exc, exc_info=True,
            )
            err = StreamChunk(type="error", content="subscribe_failed")
            yield f"data: {err.model_dump_json()}\n\n".encode("utf-8")
            yield b"data: [DONE]\n\n"
            return

        try:
            while True:
                try:
                    chunk = await asyncio.wait_for(
                        queue.get(), timeout=_SSE_HEARTBEAT_SECONDS,
                    )
                except asyncio.TimeoutError:
                    hb = StreamChunk(type="heartbeat")
                    yield (
                        f"data: {hb.model_dump_json()}\n\n".encode("utf-8")
                    )
                    continue
                except asyncio.CancelledError:
                    # Client disconnected — exit the loop quietly.
                    return

                try:
                    envelope = StreamChunk(
                        type="worker_event",
                        data=chunk.model_dump(mode="json"),
                    )
                    yield (
                        f"data: {envelope.model_dump_json()}\n\n".encode("utf-8")
                    )
                except Exception as enc_exc:  # noqa: BLE001
                    logger.warning(
                        "events_stream encode failure user=%s tenant=%s "
                        "type=%s err=%r",
                        user_id, tenant_id, type(enc_exc).__name__, enc_exc,
                    )
                    # Skip the malformed event, keep the stream alive.
                    continue
        except asyncio.CancelledError:
            return
        except Exception as exc:  # noqa: BLE001
            logger.exception(
                "events_stream loop crashed user=%s tenant=%s err=%r",
                user_id, tenant_id, exc,
            )
            try:
                err = StreamChunk(type="error", content="stream_error")
                yield f"data: {err.model_dump_json()}\n\n".encode("utf-8")
            except Exception:  # pragma: no cover
                pass
        finally:
            if queue is not None:
                try:
                    await event_bus.unsubscribe(user_id, queue)
                except Exception:  # pragma: no cover
                    pass

    return StreamingResponse(
        _generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


# ── Phase 4: trace viewer + KB stats (admin only) ─────────────────────


_VALID_TRACE_STATUS = {"pending", "completed", "partial", "failed"}


@router.get("/traces")
async def list_orchestration_traces(
    tenant_id: Optional[str] = Query(default=None),
    user_id: Optional[str] = Query(default=None),
    status_filter: Optional[str] = Query(default=None, alias="status"),
    limit: int = Query(default=20, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
    _admin: UserInfo = Depends(get_current_admin_user),
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    """Paginated list of OrchestrationTrace rows for the admin viewer.

    Each row is augmented with task_count + failed_task_count so the
    admin UI can render "5/8 tasks succeeded" without N+1 queries.
    """
    if status_filter and status_filter not in _VALID_TRACE_STATUS:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=(
                "status must be one of: " + ", ".join(sorted(_VALID_TRACE_STATUS))
            ),
        )

    stmt = select(OrchestrationTrace).order_by(
        OrchestrationTrace.created_at.desc()
    )
    if tenant_id is not None:
        stmt = stmt.where(OrchestrationTrace.tenant_id == tenant_id)
    if user_id is not None:
        stmt = stmt.where(OrchestrationTrace.user_id == user_id)
    if status_filter is not None:
        stmt = stmt.where(OrchestrationTrace.status == status_filter)

    # Total before pagination so the admin UI can show "x of N".
    count_stmt = select(func.count()).select_from(stmt.subquery())
    total = int(
        (await db.execute(count_stmt)).scalar_one_or_none() or 0
    )

    rows = (
        await db.execute(stmt.limit(limit).offset(offset))
    ).scalars().all()

    if not rows:
        return {
            "total": total,
            "limit": limit,
            "offset": offset,
            "traces": [],
        }

    # Aggregate task counts in one query keyed by trace_id.
    trace_ids = [r.trace_id for r in rows]
    task_counts = {
        tid: {"total": 0, "failed": 0} for tid in trace_ids
    }
    # Single grouped query with a CASE for the failed-count branch —
    # works on SQLite (dev) and Azure SQL (prod) without driver-specific
    # boolean coercion tricks.
    task_stmt = (
        select(
            OrchestrationTask.trace_id,
            func.count(OrchestrationTask.task_id),
            func.sum(
                case((OrchestrationTask.status == "failed", 1), else_=0)
            ),
        )
        .where(OrchestrationTask.trace_id.in_(trace_ids))
        .group_by(OrchestrationTask.trace_id)
    )
    for tid, total_n, failed_n in (await db.execute(task_stmt)).all():
        task_counts[tid] = {
            "total": int(total_n or 0),
            "failed": int(failed_n or 0),
        }

    traces = [
        {
            "trace_id": r.trace_id,
            "goal_id": r.goal_id,
            "goal": (r.plan_json or {}).get("goal"),
            "status": r.status,
            "user_id": r.user_id,
            "tenant_id": r.tenant_id,
            "profile_mode": r.profile_mode,
            "created_at": r.created_at.isoformat() if r.created_at else None,
            "completed_at": (
                r.completed_at.isoformat() if r.completed_at else None
            ),
            "task_count": task_counts[r.trace_id]["total"],
            "failed_task_count": task_counts[r.trace_id]["failed"],
        }
        for r in rows
    ]
    return {
        "total": total,
        "limit": limit,
        "offset": offset,
        "traces": traces,
    }


@router.get("/traces/{trace_id}")
async def get_orchestration_trace_detail(
    trace_id: str,
    _admin: UserInfo = Depends(get_current_admin_user),
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    """Full detail for one trace + every task under it."""
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
        "goal_id": trace.goal_id,
        "goal": (trace.plan_json or {}).get("goal"),
        "status": trace.status,
        "user_id": trace.user_id,
        "tenant_id": trace.tenant_id,
        "profile_mode": trace.profile_mode,
        "plan_json": trace.plan_json or {},
        "created_at": trace.created_at.isoformat() if trace.created_at else None,
        "completed_at": (
            trace.completed_at.isoformat() if trace.completed_at else None
        ),
        "tasks": [
            {
                "task_id": t.task_id,
                "worker_id": t.worker_id,
                "capability": t.capability,
                "execution_mode": t.execution_mode,
                "status": t.status,
                "summary": t.summary,
                "error_code": t.error_code,
                "error_message": t.error_message,
                "latency_ms": t.latency_ms,
                "created_at": (
                    t.created_at.isoformat() if t.created_at else None
                ),
                "completed_at": (
                    t.completed_at.isoformat() if t.completed_at else None
                ),
            }
            for t in rows
        ],
    }


@router.get("/kb/stats")
async def get_kb_stats(
    _admin: UserInfo = Depends(get_current_admin_user),
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    """KB health summary for admins.

    Numbers come from SQL — the source of truth for relational data.
    Search-index document count (when configured) is appended on a
    best-effort basis."""
    stats = await knowledge_store.stats(db)

    # Append Search index stats when configured.  Best-effort: any
    # failure leaves the SQL stats intact.
    try:
        from app.orchestration.knowledge_search import kb_search_client
        if kb_search_client is not None:
            stats["search_index"] = kb_search_client.stats()
    except Exception as exc:  # noqa: BLE001
        logger.debug("KB search stats skipped: %s", exc)
    return stats


# ── Phase 5C: per-tenant worker access control (admin only) ──────────────


@router.get("/access")
async def list_worker_access(
    worker_id: Optional[str] = Query(default=None),
    tenant_id: Optional[str] = Query(default=None),
    include_revoked: bool = Query(default=False),
    _admin: UserInfo = Depends(get_current_admin_user),
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    """List per-tenant access grants.

    Returns ALL grants (active + revoked) when ``include_revoked=true``;
    otherwise restricts to active grants only.  Soft-deleted grants are
    NEVER hard-deleted so admins can audit revocations forever.
    """
    stmt = select(WorkerTenantAccess).order_by(
        WorkerTenantAccess.granted_at.desc()
    )
    if worker_id is not None:
        stmt = stmt.where(WorkerTenantAccess.worker_id == worker_id)
    if tenant_id is not None:
        stmt = stmt.where(WorkerTenantAccess.tenant_id == tenant_id)
    if not include_revoked:
        stmt = stmt.where(WorkerTenantAccess.revoked_at.is_(None))

    rows = (await db.execute(stmt)).scalars().all()
    return {
        "default_allow": bool(
            getattr(settings, "WORKER_ACCESS_DEFAULT_ALLOW", True)
        ),
        "grants": [
            {
                "id": r.id,
                "worker_id": r.worker_id,
                "tenant_id": r.tenant_id,
                "granted_at": (
                    r.granted_at.isoformat() if r.granted_at else None
                ),
                "granted_by": r.granted_by,
                "revoked_at": (
                    r.revoked_at.isoformat() if r.revoked_at else None
                ),
            }
            for r in rows
        ],
    }


@router.post("/access")
async def grant_worker_access(
    body: dict[str, Any],
    admin: UserInfo = Depends(get_current_admin_user),
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    """Grant a tenant access to a worker.

    Returns 409 when an active (non-revoked) grant already exists for
    the same (worker_id, tenant_id).  Re-granting after revocation
    creates a fresh row — the audit trail keeps the old one with its
    revoked_at intact.
    """
    worker_id = (body or {}).get("worker_id")
    tenant_id = (body or {}).get("tenant_id")
    if not worker_id or not tenant_id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="worker_id and tenant_id are required",
        )

    # Confirm the worker exists — refusing to grant access to a non-
    # registered worker keeps the table consistent with the registry.
    if (await worker_registry.get(db, worker_id)) is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"worker {worker_id!r} not registered",
        )

    existing_stmt = select(WorkerTenantAccess.id).where(
        WorkerTenantAccess.worker_id == worker_id,
        WorkerTenantAccess.tenant_id == tenant_id,
        WorkerTenantAccess.revoked_at.is_(None),
    ).limit(1)
    existing = (await db.execute(existing_stmt)).scalar_one_or_none()
    if existing is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                f"active grant already exists for worker={worker_id!r} "
                f"tenant={tenant_id!r} (id={existing})"
            ),
        )

    row = WorkerTenantAccess(
        worker_id=worker_id,
        tenant_id=tenant_id,
        granted_by=str(admin.id),
    )
    db.add(row)
    await db.commit()
    await db.refresh(row)
    return {
        "id": row.id,
        "worker_id": row.worker_id,
        "tenant_id": row.tenant_id,
        "granted_at": row.granted_at.isoformat() if row.granted_at else None,
        "granted_by": row.granted_by,
        "revoked_at": None,
    }


@router.delete("/access/{access_id}")
async def revoke_worker_access(
    access_id: str,
    _admin: UserInfo = Depends(get_current_admin_user),
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    """Soft-delete a grant by setting ``revoked_at = now()``.

    Idempotent: revoking an already-revoked grant returns the grant's
    current state without bumping revoked_at.  Hard-delete is never
    exposed — audit trail must persist.
    """
    from datetime import datetime as _dt
    row = await db.get(WorkerTenantAccess, access_id)
    if row is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"access grant {access_id!r} not found",
        )
    if row.revoked_at is None:
        row.revoked_at = _dt.utcnow()
        await db.commit()
    return {
        "id": row.id,
        "worker_id": row.worker_id,
        "tenant_id": row.tenant_id,
        "revoked_at": row.revoked_at.isoformat() if row.revoked_at else None,
    }


# ── Phase 6A: MCP client management (admin only) ────────────────────────


@router.post("/mcp-clients")
async def create_mcp_client(
    body: dict[str, Any],
    admin: UserInfo = Depends(get_current_admin_user),
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    """Create an MCP client.

    Returns the plaintext API key EXACTLY ONCE.  Persists only the
    bcrypt hash.  If the caller loses the plaintext, admins must
    revoke and recreate — there is no recovery path.
    """
    from app.mcp.auth import generate_api_key, hash_api_key
    from app.mcp.tools import MELA_TOOL_NAMES, SCOPE_WILDCARD

    client_name = (body or {}).get("client_name") or ""
    client_name = client_name.strip()
    if not client_name:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="client_name is required",
        )
    raw_scopes = (body or {}).get("scopes") or []
    if not isinstance(raw_scopes, list):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="scopes must be a list of tool names",
        )
    invalid = [
        s for s in raw_scopes
        if s != SCOPE_WILDCARD and s not in MELA_TOOL_NAMES
    ]
    if invalid:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"unknown scopes: {invalid}",
        )

    plaintext = generate_api_key()
    row = MCPClient(
        client_name=client_name,
        api_key_hash=hash_api_key(plaintext),
        tenant_id=(body or {}).get("tenant_id"),
        scopes=list(raw_scopes),
        created_by=str(admin.id),
    )
    db.add(row)
    await db.commit()
    await db.refresh(row)
    return {
        "id": row.id,
        "client_name": row.client_name,
        "tenant_id": row.tenant_id,
        "scopes": row.scopes,
        "created_at": row.created_at.isoformat() if row.created_at else None,
        # First and last time this is ever returned.
        "api_key": plaintext,
    }


@router.get("/mcp-clients")
async def list_mcp_clients(
    include_revoked: bool = Query(default=False),
    _admin: UserInfo = Depends(get_current_admin_user),
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    """List MCP clients (metadata only — never key values)."""
    stmt = select(MCPClient).order_by(MCPClient.created_at.desc())
    if not include_revoked:
        stmt = stmt.where(MCPClient.revoked_at.is_(None))
    rows = (await db.execute(stmt)).scalars().all()
    return {
        "clients": [
            {
                "id": r.id,
                "client_name": r.client_name,
                "tenant_id": r.tenant_id,
                "scopes": r.scopes,
                "created_at": (
                    r.created_at.isoformat() if r.created_at else None
                ),
                "revoked_at": (
                    r.revoked_at.isoformat() if r.revoked_at else None
                ),
                "last_used_at": (
                    r.last_used_at.isoformat() if r.last_used_at else None
                ),
            }
            for r in rows
        ],
    }


@router.delete("/mcp-clients/{client_id}")
async def revoke_mcp_client(
    client_id: str,
    _admin: UserInfo = Depends(get_current_admin_user),
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    """Soft-delete an MCP client.  Idempotent."""
    from datetime import datetime as _dt
    row = await db.get(MCPClient, client_id)
    if row is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"MCP client {client_id!r} not found",
        )
    if row.revoked_at is None:
        row.revoked_at = _dt.utcnow()
        await db.commit()
    return {
        "id": row.id,
        "client_name": row.client_name,
        "revoked_at": (
            row.revoked_at.isoformat() if row.revoked_at else None
        ),
    }


# ── Phase 6C: bidirectional handshake ───────────────────────────────────


@router.get("/capabilities")
async def list_mela_capabilities() -> dict[str, Any]:
    """Public, no-auth.  Tells external apps what Mela exposes via its
    own MCP server.  Pattern: same shape Mela reads from worker
    capability manifests, so a worker discovering Mela can use the
    same code path it uses to read its own capabilities."""
    from app.mcp.tools import MELA_TOOL_DEFS
    return {"tools": MELA_TOOL_DEFS}


@router.post("/register")
async def register_worker(
    body: dict[str, Any],
    x_registration_key: str = Header(default="", alias="X-Registration-Key"),
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    """Worker self-registration handshake.

    Disabled by default (``MELA_WORKER_REGISTRATION_KEY`` blank →
    503).  When configured: the worker presents the shared secret in
    ``X-Registration-Key``, posts a ``WorkerManifest`` body, gets back
    a freshly-generated ``inbound_api_key`` to use on
    ``/api/v1/ingest/*`` callbacks.  Re-registering with the same id
    bumps the manifest and invalidates the registry cache.
    """
    expected_key = (settings.MELA_WORKER_REGISTRATION_KEY or "").strip()
    if not expected_key:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=(
                "Worker self-registration is disabled.  Set "
                "MELA_WORKER_REGISTRATION_KEY in environment to enable."
            ),
        )
    import hmac
    if not hmac.compare_digest(expected_key, str(x_registration_key)):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid registration key",
        )

    # Validate the manifest by parsing it through the pydantic model.
    try:
        manifest = WorkerManifest.model_validate(body)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"invalid WorkerManifest: {exc}",
        )

    # Mint a fresh inbound API key — overwrites any previous one on
    # re-registration.  The previous key is silently invalidated so an
    # old worker process can't keep posting after a new one took over.
    import secrets
    inbound_key = "wkr_" + secrets.token_urlsafe(24)
    auth_config = dict(manifest.auth_config or {})
    auth_config["inbound_api_key"] = inbound_key
    manifest = manifest.model_copy(update={"auth_config": auth_config})

    upserted = await worker_registry.upsert(db, manifest)
    # Cache invalidation already happens inside ``upsert`` — the next
    # ``router.route`` lookup picks up the new manifest.
    return {
        "worker_id": upserted.id,
        "version": upserted.version,
        "inbound_api_key": inbound_key,
        "report_back_url": upserted.report_back_url,
    }
