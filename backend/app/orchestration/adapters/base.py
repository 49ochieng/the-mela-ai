"""
Mela AI - Worker adapter base class.

Every worker has exactly one adapter.  An adapter has one job:
translate a ``MelaTask`` into the worker's native call shape, execute
it, and translate the response back into a ``MelaResult``.  Adapters
are the ONLY code that knows a specific worker's protocol details —
nothing else in orchestration imports worker-specific clients.

Hard contract:
  * ``execute()`` must NEVER raise — it always returns a ``MelaResult``.
    Use ``MelaResult.failure(...)`` for the failure path.
  * The base class wraps every call with the per-worker circuit
    breaker, so subclasses only worry about doing the call once.
  * Subclasses implement ``_dispatch()`` which is allowed to raise;
    the base ``execute()`` catches everything, records breaker state,
    applies the manifest's retry policy, and emits a normalized
    ``MelaResult``.
"""

from __future__ import annotations

import asyncio
import logging
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Optional

from app.orchestration.breaker import CircuitBreaker, circuit_breaker
from app.orchestration.types import (
    MelaError,
    MelaResult,
    MelaResultMetadata,
    MelaTask,
    WorkerManifest,
)

logger = logging.getLogger(__name__)


@dataclass
class AdapterHealth:
    """Result of a one-off liveness probe."""

    healthy: bool
    latency_ms: int
    detail: str


class WorkerAdapter(ABC):
    """Base class every worker adapter inherits from."""

    def __init__(
        self,
        manifest: WorkerManifest,
        breaker: Optional[CircuitBreaker] = None,
    ) -> None:
        self.manifest = manifest
        self._breaker = breaker or circuit_breaker

    @property
    def worker_id(self) -> str:
        return self.manifest.id

    # ── Public, never-raising entry point ────────────────────────────────

    async def execute(self, task: MelaTask) -> MelaResult:
        """Run the task. Always returns a MelaResult; never raises."""
        if task.worker_id != self.manifest.id:
            return MelaResult.failure(
                task=task,
                code="WORKER_MISMATCH",
                message=(
                    f"task.worker_id={task.worker_id!r} does not match "
                    f"adapter manifest id={self.manifest.id!r}"
                ),
                retryable=False,
                source=self.manifest.id,
            )

        if not self.manifest.has_capability(task.capability):
            return MelaResult.failure(
                task=task,
                code="UNKNOWN_CAPABILITY",
                message=(
                    f"capability={task.capability!r} not registered for "
                    f"worker={self.manifest.id!r}"
                ),
                retryable=False,
                source=self.manifest.id,
            )

        if not await self._breaker.allow(self.manifest.id):
            return MelaResult.failure(
                task=task,
                code="BREAKER_OPEN",
                message=(
                    f"worker={self.manifest.id!r} is temporarily unavailable "
                    "after recent failures; the circuit breaker will retry "
                    "in ~30s"
                ),
                retryable=True,
                source=self.manifest.id,
            )

        return await self._execute_with_retry(task)

    # ── Subclass hooks ───────────────────────────────────────────────────

    @abstractmethod
    async def _dispatch(self, task: MelaTask) -> MelaResult:
        """Translate MelaTask → worker call → MelaResult.

        Subclasses are allowed to raise here; the base class catches
        and converts to a MelaResult.  Always populate ``metadata`` and
        a brief ``summary`` on success.
        """

    @abstractmethod
    async def health_check(self) -> AdapterHealth:
        """One-shot liveness probe.  Should never raise."""

    def is_retryable(self, error: BaseException) -> bool:
        """Subclass hook — classify an exception as retryable.

        Default: timeout / connection errors are retryable, everything
        else isn't.  Subclasses override to add worker-specific cases.
        """
        if isinstance(error, asyncio.TimeoutError):
            return True
        msg = type(error).__name__.lower()
        return any(
            tag in msg
            for tag in ("timeout", "connect", "network", "transport", "remote")
        )

    # ── Retry + breaker accounting ───────────────────────────────────────

    async def _execute_with_retry(self, task: MelaTask) -> MelaResult:
        retry = self.manifest.retry_policy
        attempt = 0
        backoff_ms = retry.backoff_ms
        last_error: Optional[BaseException] = None
        started = time.monotonic()

        while attempt < retry.max_attempts:
            attempt += 1
            try:
                result = await self._dispatch(task)
            except BaseException as exc:  # noqa: BLE001 — adapters must not raise
                last_error = exc
                logger.warning(
                    "adapter dispatch raised: worker=%s capability=%s "
                    "attempt=%d err=%s",
                    self.manifest.id,
                    task.capability,
                    attempt,
                    exc,
                )
                if not self.is_retryable(exc) or attempt >= retry.max_attempts:
                    await self._breaker.record_failure(self.manifest.id)
                    return self._exception_to_result(task, exc, started)
                await asyncio.sleep(backoff_ms / 1000.0)
                backoff_ms = int(backoff_ms * retry.backoff_multiplier)
                continue

            # Dispatch returned cleanly — but may itself report failure.
            if result.success:
                await self._breaker.record_success(self.manifest.id)
                return result

            # Failure result.  Retry if the adapter flagged it retryable.
            if (
                result.error is not None
                and result.error.retryable
                and attempt < retry.max_attempts
            ):
                await asyncio.sleep(backoff_ms / 1000.0)
                backoff_ms = int(backoff_ms * retry.backoff_multiplier)
                continue

            await self._breaker.record_failure(self.manifest.id)
            return result

        # Loop exhausted
        if last_error is not None:
            return self._exception_to_result(task, last_error, started)
        return MelaResult.failure(
            task=task,
            code="RETRY_EXHAUSTED",
            message=f"all {retry.max_attempts} attempts failed",
            retryable=False,
            latency_ms=int((time.monotonic() - started) * 1000),
            source=self.manifest.id,
        )

    def _exception_to_result(
        self,
        task: MelaTask,
        exc: BaseException,
        started_monotonic: float,
    ) -> MelaResult:
        latency_ms = int((time.monotonic() - started_monotonic) * 1000)
        return MelaResult(
            task_id=task.task_id,
            trace_id=task.trace_id,
            worker_id=self.manifest.id,
            capability=task.capability,
            success=False,
            data={},
            summary=f"{task.capability} failed: {type(exc).__name__}: {exc}",
            metadata=MelaResultMetadata(
                latency_ms=latency_ms, source=self.manifest.id
            ),
            error=MelaError(
                code="ADAPTER_EXCEPTION",
                message=f"{type(exc).__name__}: {exc}",
                retryable=self.is_retryable(exc),
            ),
        )
