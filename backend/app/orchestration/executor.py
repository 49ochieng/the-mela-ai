"""
Mela AI - Executor.

Runs a plan of MelaTasks: sequential batches, parallel within each
batch via ``asyncio.gather(..., return_exceptions=True)``.  Persists
``OrchestrationTrace`` + ``OrchestrationTask`` rows so every call is
visible in admin tooling and can be queried by ``trace_id``.

Phase 2 surface
---------------

* ``run_single(db, task)`` — issue exactly one ``MelaTask`` (the path
  used by the tool-call dispatch in ``tool_executor``) and return its
  ``MelaResult``.  Persists a one-task trace.
* ``run_plan(db, plan)`` — execute an ``ExecutionPlan`` of batches.
  Within each batch tasks fire in parallel; between batches the
  executor waits for the previous batch to complete (a la
  ``Promise.allSettled``-style semantics).

Hard rules
----------

* Never raises.  A failing adapter produces a failed ``MelaResult``,
  which is persisted and returned alongside any successful peers.
* No retry layer here.  The adapter base already retries per the
  manifest's policy; the breaker is the fail-fast gate.  See
  ``orchestration/breaker.py`` and ``orchestration/adapters/base.py``.
* Tasks emitted in ``execution_mode="async"`` are registered with the
  ``OrchestrationStore`` BEFORE the call goes out, so a fast worker
  callback can never race the registration.
"""

from __future__ import annotations

import asyncio
import logging
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from typing import Awaitable, Callable, Optional

from sqlalchemy.ext.asyncio import AsyncSession

from app.models.models import OrchestrationTask, OrchestrationTrace
from app.orchestration.router import RouteFailure, Router, router as default_router

# Per-session lock so concurrent ``_dispatch_one`` coros sharing one
# AsyncSession serialize their persist calls.  Without this, two parallel
# coros calling ``db.add(...)`` + ``db.commit()`` on the same session
# trigger ``SAWarning: Session.add() during flush``.  Stored on the
# session instance itself so each request's session gets its own lock
# without us holding references that would block GC.
def _session_lock(db: AsyncSession) -> asyncio.Lock:
    lock = getattr(db, "_orch_persist_lock", None)
    if lock is None:
        lock = asyncio.Lock()
        try:
            setattr(db, "_orch_persist_lock", lock)
        except Exception:
            # Some session proxies forbid attribute setting; fall back to
            # a fresh lock per call (no serialization, but no warning
            # either since the proxy is single-use).
            return asyncio.Lock()
    return lock
from app.orchestration.store import (
    OrchestrationStore,
    PendingTask,
    orchestration_store as default_store,
)
from app.orchestration.types import MelaResult, MelaTask

logger = logging.getLogger(__name__)


# Optional progress callback signature: called once per task as it transitions
# from "started" to "completed" so tool_executor can stream tool_executing /
# tool_result chunks to the chat UI without coupling the executor to SSE.
ProgressCallback = Callable[[MelaTask, Optional[MelaResult], str], Awaitable[None]]


# ── Plan types ───────────────────────────────────────────────────────────


@dataclass
class TaskBatch:
    """One parallel batch within an ExecutionPlan."""

    batch_index: int
    tasks: list[MelaTask]
    depends_on: list[str] = field(default_factory=list)


@dataclass
class ExecutionPlan:
    """An ordered DAG of batches that satisfy a single goal."""

    plan_id: str
    goal_id: str
    goal: str
    batches: list[TaskBatch]
    user_id: str
    tenant_id: Optional[str] = None
    profile_mode: str = "personal"
    created_at: datetime = field(default_factory=datetime.utcnow)


@dataclass
class ExecutionResult:
    """Aggregate of every MelaResult produced by an ExecutionPlan."""

    plan_id: str
    trace_id: str
    results: list[MelaResult]

    @property
    def success(self) -> bool:
        return all(r.success for r in self.results)

    @property
    def partial(self) -> bool:
        return any(r.success for r in self.results) and not self.success


# ── Executor ─────────────────────────────────────────────────────────────


class Executor:
    """Drives MelaTask execution, persistence, and async-callback registration."""

    def __init__(
        self,
        router: Optional[Router] = None,
        store: Optional[OrchestrationStore] = None,
    ) -> None:
        self._router = router or default_router
        self._store = store or default_store

    # ── Single-task path (used by tool dispatch) ─────────────────────────

    async def run_single(
        self,
        db: AsyncSession,
        task: MelaTask,
        *,
        trace_id: Optional[str] = None,
        goal: str = "",
        on_progress: Optional[ProgressCallback] = None,
    ) -> MelaResult:
        """Execute one MelaTask under its own trace and return the result.

        The trace_id on ``task`` is canonical — if the caller already minted
        one (e.g. chat_service propagating its _corr_id), pass it via the
        ``trace_id`` kwarg or set it directly on ``task.trace_id`` before
        calling.  Either way, the persisted trace uses ``task.trace_id``.
        """
        if trace_id:
            task = task.model_copy(update={"trace_id": trace_id})

        await self._upsert_trace(
            db,
            trace_id=task.trace_id,
            goal_id=task.context.goal_id,
            goal=goal or task.capability,
            user_id=task.context.user_id,
            tenant_id=task.context.tenant_id,
            profile_mode=("work" if task.context.tenant_id else "personal"),
            plan_json={"single_task": task.task_id, "capability": task.capability},
        )
        result = await self._dispatch_one(db, task, on_progress=on_progress)
        await self._mark_trace_done(db, task.trace_id, [result])
        return result

    # ── Multi-batch path (planner output) ────────────────────────────────

    async def run_plan(
        self,
        db: AsyncSession,
        plan: ExecutionPlan,
        *,
        on_progress: Optional[ProgressCallback] = None,
    ) -> ExecutionResult:
        """Execute every batch sequentially; tasks within a batch in parallel."""
        # Use the first task's trace_id, falling back to a fresh one if the
        # plan is empty or the planner didn't seed one.
        trace_id = next(
            (t.trace_id for batch in plan.batches for t in batch.tasks),
            str(uuid.uuid4()),
        )
        await self._upsert_trace(
            db,
            trace_id=trace_id,
            goal_id=plan.goal_id,
            goal=plan.goal,
            user_id=plan.user_id,
            tenant_id=plan.tenant_id,
            profile_mode=plan.profile_mode,
            plan_json={
                "plan_id": plan.plan_id,
                "batch_count": len(plan.batches),
                "task_count": sum(len(b.tasks) for b in plan.batches),
            },
        )

        all_results: list[MelaResult] = []

        for batch in plan.batches:
            if not batch.tasks:
                continue
            # Force the trace_id onto every task so partial planner output
            # can't leak inconsistent IDs into persistence.
            normalized = [
                t.model_copy(update={"trace_id": trace_id}) for t in batch.tasks
            ]

            # Promise.allSettled-style: gather with return_exceptions=True.
            # ``_dispatch_one`` itself never raises, so exceptions here would
            # only come from cancellation — but we keep the flag on for
            # belt-and-suspenders.
            coros = [
                self._dispatch_one(db, t, on_progress=on_progress)
                for t in normalized
            ]
            batch_results = await asyncio.gather(*coros, return_exceptions=True)

            for task, outcome in zip(normalized, batch_results):
                if isinstance(outcome, BaseException):
                    logger.error(
                        "Executor: batch task raised (should never happen) "
                        "trace=%s task=%s err=%s",
                        trace_id, task.task_id, outcome,
                    )
                    failure = MelaResult.failure(
                        task=task,
                        code="EXECUTOR_INTERNAL_ERROR",
                        message=f"{type(outcome).__name__}: {outcome}",
                        retryable=False,
                        source="executor",
                    )
                    all_results.append(failure)
                else:
                    all_results.append(outcome)

        await self._mark_trace_done(db, trace_id, all_results)
        return ExecutionResult(
            plan_id=plan.plan_id,
            trace_id=trace_id,
            results=all_results,
        )

    # ── Internal: dispatch one task ──────────────────────────────────────

    async def _dispatch_one(
        self,
        db: AsyncSession,
        task: MelaTask,
        *,
        on_progress: Optional[ProgressCallback],
    ) -> MelaResult:
        # Persist the task row up front so admins / status queries can see
        # it even if the call hangs.
        await self._insert_task_row(db, task)
        await self._safe_progress(on_progress, task, None, "started")

        outcome = await self._router.route(db, task)
        if isinstance(outcome, RouteFailure):
            result = outcome.result
            await self._update_task_row(db, result)
            await self._write_error_log_if_needed(db, task, result)
            await self._safe_progress(on_progress, task, result, "completed")
            return result

        adapter = outcome

        # Async path: register the pending task BEFORE the call goes out so
        # a fast worker callback can never arrive ahead of the registration.
        if task.execution_mode == "async":
            await self._store.register(
                PendingTask(
                    trace_id=task.trace_id,
                    task_id=task.task_id,
                    worker_id=task.worker_id,
                    capability=task.capability,
                )
            )

        result = await adapter.execute(task)

        if task.execution_mode == "async" and result.success:
            # The adapter returned a "worker accepted async job" success;
            # the real result lands later via /ingest/result.  Keep the
            # task row in awaiting_callback state.
            await self._update_task_row(
                db, result, status_override="awaiting_callback"
            )
        else:
            await self._update_task_row(db, result)

        await self._write_error_log_if_needed(db, task, result)
        await self._safe_progress(on_progress, task, result, "completed")
        return result

    # ── DB helpers ───────────────────────────────────────────────────────

    async def _upsert_trace(
        self,
        db: AsyncSession,
        *,
        trace_id: str,
        goal_id: str,
        goal: str,
        user_id: str,
        tenant_id: Optional[str],
        profile_mode: str,
        plan_json: dict,
    ) -> None:
        try:
            existing = await db.get(OrchestrationTrace, trace_id)
            if existing is not None:
                # Idempotent upsert — re-running run_single under the same
                # trace_id (e.g. an explicit retry) updates plan_json only.
                existing.plan_json = plan_json
                existing.status = "pending"
                await db.commit()
                return
            db.add(
                OrchestrationTrace(
                    trace_id=trace_id,
                    goal_id=goal_id,
                    user_id=user_id,
                    tenant_id=tenant_id,
                    profile_mode=profile_mode,
                    status="pending",
                    plan_json=plan_json,
                )
            )
            await db.commit()
        except Exception as exc:  # noqa: BLE001 — persistence must not break execution
            logger.warning("OrchestrationTrace upsert failed: %s", exc)
            try:
                await db.rollback()
            except Exception:
                pass

        # Audit the plan creation per session decision: orchestration.* events go
        # to AuditLog, no new audit table needed.
        await self._audit(
            db,
            user_id=user_id,
            tenant_id=tenant_id,
            event_type="orchestration.plan_created",
            payload={
                "trace_id": trace_id,
                "goal_id": goal_id,
                "goal": goal[:200],
                "profile_mode": profile_mode,
            },
        )

    async def _mark_trace_done(
        self, db: AsyncSession, trace_id: str, results: list[MelaResult]
    ) -> None:
        if not results:
            status = "completed"
        elif all(r.success for r in results):
            status = "completed"
        elif any(r.success for r in results):
            status = "partial"
        else:
            status = "failed"
        try:
            row = await db.get(OrchestrationTrace, trace_id)
            if row is None:
                return
            row.status = status
            row.completed_at = datetime.utcnow()
            await db.commit()
        except Exception as exc:  # noqa: BLE001
            logger.warning("OrchestrationTrace finalize failed: %s", exc)
            try:
                await db.rollback()
            except Exception:
                pass

    async def _insert_task_row(self, db: AsyncSession, task: MelaTask) -> None:
        async with _session_lock(db):
            try:
                db.add(
                    OrchestrationTask(
                        task_id=task.task_id,
                        trace_id=task.trace_id,
                        worker_id=task.worker_id,
                        capability=task.capability,
                        execution_mode=task.execution_mode,
                        status="running",
                        params_json=dict(task.params or {}),
                    )
                )
                await db.commit()
            except Exception as exc:  # noqa: BLE001
                logger.warning("OrchestrationTask insert failed: %s", exc)
                try:
                    await db.rollback()
                except Exception:
                    pass

    async def _update_task_row(
        self,
        db: AsyncSession,
        result: MelaResult,
        *,
        status_override: Optional[str] = None,
    ) -> None:
        async with _session_lock(db):
            await self._update_task_row_locked(
                db, result, status_override=status_override
            )

    async def _update_task_row_locked(
        self,
        db: AsyncSession,
        result: MelaResult,
        *,
        status_override: Optional[str] = None,
    ) -> None:
        try:
            row = await db.get(OrchestrationTask, result.task_id)
            if row is None:
                # Race: caller is updating a row we never inserted (e.g. a
                # callback for an async task that landed in a different
                # process).  Insert a synthetic row so status queries see it.
                db.add(
                    OrchestrationTask(
                        task_id=result.task_id,
                        trace_id=result.trace_id,
                        worker_id=result.worker_id,
                        capability=result.capability,
                        execution_mode="sync",
                        status=status_override or (
                            "completed" if result.success else "failed"
                        ),
                        params_json={},
                        summary=result.summary,
                        latency_ms=result.metadata.latency_ms,
                        error_code=(result.error.code if result.error else None),
                        error_message=(result.error.message if result.error else None),
                        completed_at=datetime.utcnow(),
                    )
                )
            else:
                row.status = status_override or (
                    "completed" if result.success else "failed"
                )
                row.summary = result.summary
                row.latency_ms = result.metadata.latency_ms
                if result.error is not None:
                    row.error_code = result.error.code
                    row.error_message = result.error.message
                if status_override != "awaiting_callback":
                    row.completed_at = datetime.utcnow()
            await db.commit()
        except Exception as exc:  # noqa: BLE001
            logger.warning("OrchestrationTask update failed: %s", exc)
            try:
                await db.rollback()
            except Exception:
                pass

    async def _write_error_log_if_needed(
        self, db: AsyncSession, task: MelaTask, result: MelaResult
    ) -> None:
        async with _session_lock(db):
            await self._write_error_log_locked(db, task, result)

    async def _write_error_log_locked(
        self, db: AsyncSession, task: MelaTask, result: MelaResult
    ) -> None:
        """Funnel non-retryable adapter failures into ErrorLog.

        Per session decision: same table the admin Errors panel already
        reads from; no new error sink.  Only non-retryable failures land
        here so the panel doesn't fill up with transient rate-limit /
        timeout noise.
        """
        if result.success or result.error is None or result.error.retryable:
            return
        try:
            from app.models.models import ErrorLog
            db.add(
                ErrorLog(
                    user_id=task.context.user_id,
                    tenant_id=task.context.tenant_id,
                    method="ORCH",
                    route=f"orchestration.adapter:{task.worker_id}:{task.capability}",
                    status_code=502,
                    error_type="orchestration.adapter_failure",
                    message=result.error.message[:2000],
                    severity="error",
                    request_id=task.trace_id,
                )
            )
            await db.commit()
        except Exception as exc:  # noqa: BLE001
            logger.debug("ErrorLog write skipped: %s", exc)
            try:
                await db.rollback()
            except Exception:
                pass

    async def _audit(
        self,
        db: AsyncSession,
        *,
        user_id: str,
        tenant_id: Optional[str],
        event_type: str,
        payload: dict,
    ) -> None:
        try:
            from app.models.models import AuditLog
            db.add(
                AuditLog(
                    user_id=user_id,
                    action=event_type,
                    event_type=event_type,
                    resource_type="orchestration",
                    resource_id=payload.get("trace_id"),
                    details=payload,
                    workspace_id=tenant_id,
                    success=True,
                )
            )
            await db.commit()
        except Exception as exc:  # noqa: BLE001
            logger.debug("AuditLog write skipped (%s): %s", event_type, exc)
            try:
                await db.rollback()
            except Exception:
                pass

    @staticmethod
    async def _safe_progress(
        cb: Optional[ProgressCallback],
        task: MelaTask,
        result: Optional[MelaResult],
        phase: str,
    ) -> None:
        if cb is None:
            return
        try:
            await cb(task, result, phase)
        except Exception as exc:  # noqa: BLE001 — progress callback is best-effort
            logger.debug("progress callback raised (ignored): %s", exc)


# Module-level singleton — one executor per process.
executor = Executor()
