"""
Mela AI - Orchestration ingestion endpoints.

Inbound from registered workers ONLY.  Three routes:

  POST /api/v1/ingest/result           — async task completion
  POST /api/v1/ingest/event            — unsolicited push event
  GET  /api/v1/ingest/status/{trace}   — poll a trace's task statuses

All three require ``require_worker_api_key`` — a worker presents
``X-Worker-Id`` + ``X-Worker-Api-Key`` headers; the manifest's
``auth_config["inbound_api_key"]`` must match.  These paths are also
added to ``RateLimitMiddleware._SILENT_PATHS`` (in middleware.py) so
worker callbacks aren't capped against the human-traffic limit.
"""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Any, Optional

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.models.models import (
    NotificationType,
    OrchestrationTask,
    OrchestrationTrace,
    WorkerEvent,
)
from app.orchestration.auth import require_worker_api_key
from app.orchestration.store import orchestration_store
from app.orchestration.types import (
    MelaError,
    MelaResult,
    MelaResultMetadata,
    WorkerManifest,
)

logger = logging.getLogger(__name__)
router = APIRouter()


# ── Request schemas ──────────────────────────────────────────────────────


class IngestResultRequest(BaseModel):
    """Inbound payload for an async task completion.

    Mirrors :class:`MelaResult` — workers can submit either the full
    canonical shape OR a minimal {task_id, success, summary, data}
    subset; missing fields are filled with sensible defaults.
    """

    model_config = ConfigDict(extra="ignore")

    task_id: str
    trace_id: Optional[str] = None
    capability: Optional[str] = None
    success: bool
    data: dict[str, Any] = Field(default_factory=dict)
    summary: Optional[str] = None
    latency_ms: int = 0
    error_code: Optional[str] = None
    error_message: Optional[str] = None
    error_retryable: bool = False


class IngestEventRequest(BaseModel):
    """Inbound payload for an unsolicited worker event."""

    model_config = ConfigDict(extra="ignore")

    event_type: str
    payload: dict[str, Any] = Field(default_factory=dict)
    user_id: Optional[str] = None
    tenant_id: Optional[str] = None


# ── POST /ingest/result ──────────────────────────────────────────────────


@router.post("/ingest/result")
async def ingest_result(
    body: IngestResultRequest,
    worker: WorkerManifest = Depends(require_worker_api_key),
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    """Receive an async task's completion payload.

    Resolves the matching :class:`PendingTask` in the
    :class:`OrchestrationStore` so any sync awaiter wakes up, then
    persists the result onto the corresponding ``OrchestrationTask``
    row and notifies the user when appropriate.
    """
    pending = await orchestration_store.get(body.task_id)

    # Reject results that didn't originate from THIS worker — defends
    # against a misbehaving worker writing to another worker's task IDs.
    if pending is not None and pending.worker_id != worker.id:
        logger.warning(
            "ingest_result: worker=%s tried to resolve task=%s owned by worker=%s",
            worker.id, body.task_id, pending.worker_id,
        )
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="task_id does not belong to this worker",
        )

    trace_id = body.trace_id or (pending.trace_id if pending else "")
    capability = body.capability or (pending.capability if pending else "")

    error: Optional[MelaError] = None
    if not body.success:
        error = MelaError(
            code=body.error_code or "WORKER_REPORTED_FAILURE",
            message=body.error_message or "worker reported failure",
            retryable=body.error_retryable,
        )

    result = MelaResult(
        task_id=body.task_id,
        trace_id=trace_id,
        worker_id=worker.id,
        capability=capability or "unknown",
        success=body.success,
        data=body.data or {},
        summary=body.summary or f"{capability or 'unknown'}: callback received",
        metadata=MelaResultMetadata(latency_ms=body.latency_ms, source=worker.id),
        error=error,
    )

    # Wake any awaiter (sync caller waiting for the async result).
    resolved = await orchestration_store.complete(body.task_id, result)

    # Update the persisted OrchestrationTask row.  Reuse the executor's
    # update logic so summary/latency/error fields land in one place.
    from app.orchestration.executor import executor as _executor
    await _executor._update_task_row(db, result)

    # If every task under this trace is now terminal, mark the trace done.
    await _maybe_finalize_trace(db, trace_id)

    # Knowledge Base write — only for successful results.  Failed
    # callbacks aren't worth remembering and would just pollute search.
    if body.success:
        await _ingest_to_knowledge_base(db, worker=worker, result=result)

    # Phase 5A: real-time push to the user's SSE event stream.
    # Best-effort: failures here never break ingest.
    if body.success:
        await _publish_worker_event(db, worker=worker, result=result)

    # User-facing notification — async completions are silent unless we
    # surface them.  Phase 2 ships the SYSTEM-typed notification; Phase 2B
    # will add a dedicated WORKER_SCAN_COMPLETE enum value + tile.
    if resolved and pending is not None:
        await _notify_user(db, worker=worker, result=result)

    return {
        "accepted": True,
        "task_id": body.task_id,
        "resolved_pending": resolved,
        "trace_id": trace_id,
    }


# ── POST /ingest/event ───────────────────────────────────────────────────


@router.post("/ingest/event")
async def ingest_event(
    body: IngestEventRequest,
    worker: WorkerManifest = Depends(require_worker_api_key),
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    """Receive an unsolicited push event from a worker."""
    event = WorkerEvent(
        worker_id=worker.id,
        event_type=body.event_type,
        payload_json=body.payload or {},
        user_id=body.user_id,
        tenant_id=body.tenant_id,
    )
    db.add(event)
    await db.commit()

    # Audit per session decision: orchestration.* events go to AuditLog.
    try:
        from app.models.models import AuditLog
        db.add(
            AuditLog(
                user_id=body.user_id or "system",
                action="orchestration.worker_event",
                event_type="orchestration.worker_event",
                resource_type="worker",
                resource_id=worker.id,
                details={
                    "event_type": body.event_type,
                    "worker_id": worker.id,
                    "payload": body.payload,
                },
                workspace_id=body.tenant_id,
                success=True,
            )
        )
        await db.commit()
    except Exception as exc:  # noqa: BLE001
        logger.debug("AuditLog write skipped: %s", exc)
        try:
            await db.rollback()
        except Exception:
            pass

    # Best-effort user-facing surface for high-signal events.  Workers
    # decide which events warrant a notification by including
    # `notify: true` in the payload.
    if body.user_id and body.payload.get("notify"):
        try:
            from app.services import notification_service
            await notification_service.create_notification(
                db,
                user_id=body.user_id,
                type=NotificationType.SYSTEM,
                title=f"{worker.display_name}: {body.event_type}",
                message=str(body.payload.get("message") or body.event_type)[:500],
                link_type=None,
                link_id=None,
                actor_id=None,
                send_email=False,
            )
            await db.commit()
        except Exception as exc:  # noqa: BLE001
            logger.debug("event notification skipped: %s", exc)

        # Phase 5A: real-time push for notify-true unsolicited events.
        # Different from the result path above: there's no result/trace
        # to consult, so we mint the chunk straight from the payload.
        try:
            from app.orchestration.event_bus import event_bus
            from app.schemas.chat import WorkerEventChunk, WorkerEventType
            etype_raw = (body.event_type or "").lower()
            try:
                etype = WorkerEventType(etype_raw)
            except ValueError:
                etype = WorkerEventType.TASK_UPDATED
            chunk = WorkerEventChunk(
                worker_id=worker.id,
                event_type=etype,
                title=f"{worker.display_name}: {body.event_type}",
                summary=str(
                    body.payload.get("message") or body.event_type
                )[:500],
                trace_id=None,
            )
            await event_bus.publish(body.user_id, chunk)
        except Exception as exc:  # noqa: BLE001
            logger.debug("event bus publish skipped: %s", exc)

    return {"accepted": True, "event_id": event.id}


# ── GET /ingest/status/{trace_id} ────────────────────────────────────────


@router.get("/ingest/status/{trace_id}")
async def ingest_status(
    trace_id: str,
    worker: WorkerManifest = Depends(require_worker_api_key),
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    """Poll a trace's status.  Workers only see traces that have at
    least one task addressed to themselves — prevents leaking
    cross-worker task layouts."""
    trace = await db.get(OrchestrationTrace, trace_id)
    if trace is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"trace {trace_id!r} not found",
        )

    rows = (
        await db.execute(
            select(OrchestrationTask).where(OrchestrationTask.trace_id == trace_id)
        )
    ).scalars().all()

    # Authz: refuse if no task on this trace belongs to the calling worker.
    if not any(r.worker_id == worker.id for r in rows):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="trace does not include a task for this worker",
        )

    return {
        "trace_id": trace.trace_id,
        "goal_id": trace.goal_id,
        "status": trace.status,
        "created_at": trace.created_at.isoformat(),
        "completed_at": (
            trace.completed_at.isoformat() if trace.completed_at else None
        ),
        "tasks": [
            {
                "task_id": r.task_id,
                "worker_id": r.worker_id,
                "capability": r.capability,
                "status": r.status,
                "execution_mode": r.execution_mode,
                "summary": r.summary,
                "error_code": r.error_code,
                "latency_ms": r.latency_ms,
                "created_at": r.created_at.isoformat(),
                "completed_at": (
                    r.completed_at.isoformat() if r.completed_at else None
                ),
            }
            for r in rows
        ],
    }


# ── Internal helpers ─────────────────────────────────────────────────────


# Capability prefix → KnowledgeEntry.entry_type.  Anything not matched
# falls through to "task_summary" (the catch-all for adapter results).
_CAPABILITY_TYPE_HINTS = {
    "scan": "task_summary",
    "task": "task_summary",
    "meeting": "meeting_summary",
    "calendar": "meeting_summary",
    "transcript": "meeting_summary",
}


def _entry_type_for(capability: str) -> str:
    cap = (capability or "").lower()
    for needle, kind in _CAPABILITY_TYPE_HINTS.items():
        if needle in cap:
            return kind
    return "task_summary"


async def _ingest_to_knowledge_base(
    db: AsyncSession,
    *,
    worker: WorkerManifest,
    result: MelaResult,
) -> None:
    """Persist a successful worker result into the Knowledge Base.

    Best-effort — any failure here is logged and swallowed.  The chat
    path can still answer the user from the live result; the KB just
    won't have it for next time.
    """
    try:
        from app.orchestration.knowledge import (
            KBEntry,
            knowledge_store,
            summarise_if_needed,
        )

        # Resolve the user/tenant from the trace row, NOT from anything
        # the worker echoed in its callback body.
        task_row = await db.get(OrchestrationTask, result.task_id)
        if task_row is None:
            return
        trace_row = await db.get(OrchestrationTrace, task_row.trace_id)
        if trace_row is None or not trace_row.user_id:
            return

        # Summary already on MelaResult, but if the worker returned a
        # huge raw blob via worker_id-specific summarisation rules we
        # may still want to summarise.  Adapter base summarises
        # one-liners only; anything beyond 500 chars goes through the
        # LLM hook here.
        raw_summary = result.summary or ""
        if len(raw_summary) <= 0 and result.data:
            # Fallback summary derived from data keys when adapter didn't
            # produce one.  Keep it tiny; the real intelligence is in
            # the planner reading these.
            keys = list(result.data.keys())[:3]
            raw_summary = f"{result.capability}: keys={keys}"
        summary = await summarise_if_needed(raw_summary, worker.id)

        entry = KBEntry(
            user_id=trace_row.user_id,
            tenant_id=trace_row.tenant_id,
            profile_mode=trace_row.profile_mode or "personal",
            source_worker_id=worker.id,
            trace_id=trace_row.trace_id,
            entry_type=_entry_type_for(result.capability),
            title=f"{worker.display_name}: {result.capability}",
            summary=summary,
            data_pointer=(
                f"{worker.id}:"
                f"{(result.data or {}).get('id') or result.task_id}"
            ),
            tags=[worker.id, result.capability],
        )
        await knowledge_store.ingest(db, entry)
    except Exception as exc:  # noqa: BLE001 — KB write must not block ingest
        logger.warning("KB ingest skipped: %s", exc)
        try:
            await db.rollback()
        except Exception:
            pass


# Capability prefix → WorkerEventType.  Mirrors _entry_type_for above
# but maps onto the live-stream enum used by the frontend.
_CAPABILITY_EVENT_HINTS = {
    "scan":       "scan_completed",
    "meeting":    "meeting_ended",
    "transcript": "meeting_ended",
    "task":       "task_updated",
}


def _event_type_for(capability: str) -> str:
    cap = (capability or "").lower()
    for needle, kind in _CAPABILITY_EVENT_HINTS.items():
        if needle in cap:
            return kind
    return "task_updated"


async def _publish_worker_event(
    db: AsyncSession,
    *,
    worker: WorkerManifest,
    result: MelaResult,
) -> None:
    """Push a ``WorkerEventChunk`` onto the user's SSE channel.

    Resolves user_id via the persisted trace row — never trusts what
    the worker echoed back.  Best-effort: any failure (missing trace,
    bus error) is logged and swallowed.  The ingest path must not
    fail because the event bus had a hiccup.
    """
    try:
        from app.orchestration.event_bus import event_bus
        from app.schemas.chat import WorkerEventChunk, WorkerEventType

        task_row = await db.get(OrchestrationTask, result.task_id)
        if task_row is None:
            return
        trace_row = await db.get(OrchestrationTrace, task_row.trace_id)
        if trace_row is None or not trace_row.user_id:
            return

        event_type_str = _event_type_for(result.capability)
        try:
            event_type = WorkerEventType(event_type_str)
        except ValueError:
            event_type = WorkerEventType.TASK_UPDATED

        chunk = WorkerEventChunk(
            worker_id=worker.id,
            event_type=event_type,
            title=f"{worker.display_name}: {result.capability}",
            summary=(result.summary or "")[:500],
            trace_id=trace_row.trace_id,
        )
        await event_bus.publish(trace_row.user_id, chunk)
    except Exception as exc:  # noqa: BLE001
        logger.debug("event_bus publish skipped: %s", exc)


async def _maybe_finalize_trace(db: AsyncSession, trace_id: str) -> None:
    """Move a trace to completed/partial/failed once all its tasks settle."""
    if not trace_id:
        return
    try:
        rows = (
            await db.execute(
                select(OrchestrationTask).where(OrchestrationTask.trace_id == trace_id)
            )
        ).scalars().all()
        if not rows:
            return
        terminal = {"completed", "failed"}
        if any(r.status not in terminal for r in rows):
            return
        success_count = sum(1 for r in rows if r.status == "completed")
        if success_count == len(rows):
            new_status = "completed"
        elif success_count == 0:
            new_status = "failed"
        else:
            new_status = "partial"

        trace = await db.get(OrchestrationTrace, trace_id)
        if trace is not None and trace.status != new_status:
            trace.status = new_status
            trace.completed_at = datetime.utcnow()
            await db.commit()
    except Exception as exc:  # noqa: BLE001
        logger.debug("trace finalize skipped: %s", exc)
        try:
            await db.rollback()
        except Exception:
            pass


async def _notify_user(
    db: AsyncSession, *, worker: WorkerManifest, result: MelaResult
) -> None:
    """Best-effort in-app notification for an async completion."""
    try:
        from app.orchestration.executor import executor as _executor
        # Resolve the user_id/tenant_id from the persisted task row so we
        # don't trust whatever the worker echoed in the result.
        task_row = await db.get(OrchestrationTask, result.task_id)
        if task_row is None:
            return
        trace_row = await db.get(OrchestrationTrace, task_row.trace_id)
        if trace_row is None or not trace_row.user_id:
            return
        from app.services import notification_service
        title = f"{worker.display_name}: {result.capability}"
        message = result.summary or (
            f"Async task completed ({worker.display_name})"
        )
        await notification_service.create_notification(
            db,
            user_id=trace_row.user_id,
            type=NotificationType.WORKER_SCAN_COMPLETE,
            title=title[:200],
            message=message[:500],
            link_type=None,
            link_id=None,
            actor_id=None,
            send_email=False,
        )
        await db.commit()
        # Keep the linter happy on the unused import — _executor is the
        # canonical source for shared logic; future call sites will use it.
        _ = _executor
    except Exception as exc:  # noqa: BLE001
        logger.debug("ingest notification skipped: %s", exc)
        try:
            await db.rollback()
        except Exception:
            pass
