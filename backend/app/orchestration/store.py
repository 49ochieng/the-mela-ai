"""
Mela AI - Orchestration runtime store.

In-process registry of in-flight async tasks waiting on a worker
callback to ``/api/v1/ingest/result``.  When a task is issued in
``execution_mode="async"``, the executor records a pending entry here
with an ``asyncio.Event``; when the worker POSTs its result, the
ingestion endpoint resolves the entry, attaches the result, and sets
the event so anyone awaiting it wakes up.

This module is the ONLY place async-task waiting state lives.  It sits
behind a small ABC so that swapping in a Redis-backed store later is a
one-file change — the same migration story as ``BreakerStore``.

Phase 2 ships ``InMemoryOrchestrationStore`` only.  Multi-instance
deployments must replace this with a Redis-backed implementation
(pub/sub for the wake-up signal, JSON-encoded snapshot for the value).
"""

from __future__ import annotations

import asyncio
import logging
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Optional

from app.orchestration.types import MelaResult

logger = logging.getLogger(__name__)


@dataclass
class PendingTask:
    """An async task that has been issued and is awaiting a worker callback."""

    trace_id: str
    task_id: str
    worker_id: str
    capability: str
    issued_at: float = field(default_factory=time.monotonic)
    result: Optional[MelaResult] = None
    # Set when the result lands so anyone awaiting wait_for_result(...) wakes up.
    completed: asyncio.Event = field(default_factory=asyncio.Event)


# ── Store interface ──────────────────────────────────────────────────────


class OrchestrationStore(ABC):
    """Pluggable persistence for in-flight async tasks.

    A future ``RedisOrchestrationStore`` implements the same five methods;
    nothing in the executor / ingestion API cares which store backs them.
    """

    @abstractmethod
    async def register(self, pending: PendingTask) -> None: ...

    @abstractmethod
    async def complete(self, task_id: str, result: MelaResult) -> bool:
        """Resolve a pending task. Returns True if the task was found."""

    @abstractmethod
    async def get(self, task_id: str) -> Optional[PendingTask]: ...

    @abstractmethod
    async def list_for_trace(self, trace_id: str) -> list[PendingTask]: ...

    @abstractmethod
    async def wait_for_result(
        self, task_id: str, timeout_s: float
    ) -> Optional[MelaResult]:
        """Block until the result lands or the timeout elapses.

        Returns the ``MelaResult`` on success, ``None`` on timeout or if
        the task was never registered.
        """


class InMemoryOrchestrationStore(OrchestrationStore):
    """Single-process store.

    Correct on Azure App Service while Mela runs as a single instance.
    Will silently lose pending tasks across instances if you scale out —
    swap in a Redis-backed store at that point; nothing else changes.
    """

    def __init__(self) -> None:
        self._by_task: dict[str, PendingTask] = {}
        self._by_trace: dict[str, set[str]] = {}
        self._lock = asyncio.Lock()

    async def register(self, pending: PendingTask) -> None:
        async with self._lock:
            self._by_task[pending.task_id] = pending
            self._by_trace.setdefault(pending.trace_id, set()).add(pending.task_id)

    async def complete(self, task_id: str, result: MelaResult) -> bool:
        async with self._lock:
            pending = self._by_task.get(task_id)
            if pending is None:
                return False
            pending.result = result
        # Set the event OUTSIDE the lock — awaiters may run synchronously
        # and shouldn't block other store operations.
        pending.completed.set()
        return True

    async def get(self, task_id: str) -> Optional[PendingTask]:
        async with self._lock:
            return self._by_task.get(task_id)

    async def list_for_trace(self, trace_id: str) -> list[PendingTask]:
        async with self._lock:
            ids = self._by_trace.get(trace_id, set())
            return [self._by_task[i] for i in ids if i in self._by_task]

    async def wait_for_result(
        self, task_id: str, timeout_s: float
    ) -> Optional[MelaResult]:
        pending = await self.get(task_id)
        if pending is None:
            return None
        try:
            await asyncio.wait_for(pending.completed.wait(), timeout=timeout_s)
        except asyncio.TimeoutError:
            logger.info(
                "wait_for_result timed out task=%s worker=%s capability=%s",
                pending.task_id,
                pending.worker_id,
                pending.capability,
            )
            return None
        return pending.result


# Module-level singleton — one runtime store per process.
orchestration_store: OrchestrationStore = InMemoryOrchestrationStore()
