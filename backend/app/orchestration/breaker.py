"""
Mela AI - Circuit breakers for worker calls.

Three-state breaker (CLOSED → OPEN → HALF_OPEN → CLOSED) per worker.
Adapters call ``allow()`` before issuing a worker request, then call
``record_success()`` or ``record_failure()`` afterwards.  When a worker
crosses the failure threshold the breaker trips OPEN and ``allow()``
returns False until the cooldown elapses; the next call through is
issued in HALF_OPEN as a probe and the breaker either recovers or
re-trips.

State lives behind a ``BreakerStore`` interface so the only thing that
changes when we move to multi-instance is which store backs the
breaker.  Phase 1 ships ``InMemoryBreakerStore`` only; a Redis-backed
implementation (one file, one config line) is the next-stop migration.

This module is the ONLY place circuit-breaker logic lives.  Nothing
outside ``breaker.py`` should reason about state transitions, failure
counts, or cooldowns.
"""

from __future__ import annotations

import asyncio
import logging
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from enum import Enum
from typing import Optional

logger = logging.getLogger(__name__)


def _fire_breaker_alert(
    *,
    code: str,
    severity: str,
    worker_id: str,
    message: str,
) -> None:
    """Best-effort alert for circuit breaker state transitions."""
    try:
        from app.services.alert_service import send_alert, AlertIncident
        incident = AlertIncident(
            title=f"{code}: worker={worker_id}",
            severity=severity,
            code=code,
            worker=worker_id,
            error_message=message,
        )
        try:
            loop = asyncio.get_running_loop()
            loop.create_task(send_alert(incident))
        except RuntimeError:
            asyncio.run(send_alert(incident))
    except Exception:
        pass


# ── State ────────────────────────────────────────────────────────────────


class BreakerState(str, Enum):
    CLOSED = "closed"
    OPEN = "open"
    HALF_OPEN = "half_open"


@dataclass
class BreakerSnapshot:
    """Plain-data view of breaker state, safe to serialize."""

    worker_id: str
    state: BreakerState = BreakerState.CLOSED
    failure_count: int = 0
    opened_at: Optional[float] = None  # monotonic seconds when OPEN was entered
    last_failure_at: Optional[float] = None
    last_success_at: Optional[float] = None
    failure_window: list[float] = field(default_factory=list)


# ── Store interface ──────────────────────────────────────────────────────


class BreakerStore(ABC):
    """Pluggable persistence for breaker snapshots.

    A future ``RedisBreakerStore`` implements the same three methods;
    nothing in ``CircuitBreaker`` cares which store it is.
    """

    @abstractmethod
    async def get(self, worker_id: str) -> BreakerSnapshot: ...

    @abstractmethod
    async def set(self, snapshot: BreakerSnapshot) -> None: ...

    @abstractmethod
    async def all(self) -> list[BreakerSnapshot]: ...


class InMemoryBreakerStore(BreakerStore):
    """Single-process breaker store.

    Correct on Azure App Service while we run a single instance.  Will
    silently diverge across instances if you scale out — at that point
    swap in a Redis-backed implementation; nothing else changes.
    """

    def __init__(self) -> None:
        self._data: dict[str, BreakerSnapshot] = {}
        self._lock = asyncio.Lock()

    async def get(self, worker_id: str) -> BreakerSnapshot:
        async with self._lock:
            snap = self._data.get(worker_id)
            if snap is None:
                snap = BreakerSnapshot(worker_id=worker_id)
                self._data[worker_id] = snap
            # Return a shallow copy so callers can mutate without holding the lock
            return BreakerSnapshot(
                worker_id=snap.worker_id,
                state=snap.state,
                failure_count=snap.failure_count,
                opened_at=snap.opened_at,
                last_failure_at=snap.last_failure_at,
                last_success_at=snap.last_success_at,
                failure_window=list(snap.failure_window),
            )

    async def set(self, snapshot: BreakerSnapshot) -> None:
        async with self._lock:
            self._data[snapshot.worker_id] = BreakerSnapshot(
                worker_id=snapshot.worker_id,
                state=snapshot.state,
                failure_count=snapshot.failure_count,
                opened_at=snapshot.opened_at,
                last_failure_at=snapshot.last_failure_at,
                last_success_at=snapshot.last_success_at,
                failure_window=list(snapshot.failure_window),
            )

    async def all(self) -> list[BreakerSnapshot]:
        async with self._lock:
            return [
                BreakerSnapshot(
                    worker_id=s.worker_id,
                    state=s.state,
                    failure_count=s.failure_count,
                    opened_at=s.opened_at,
                    last_failure_at=s.last_failure_at,
                    last_success_at=s.last_success_at,
                    failure_window=list(s.failure_window),
                )
                for s in self._data.values()
            ]


# ── Breaker ──────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class BreakerConfig:
    failure_threshold: int = 3        # consecutive-window failures to trip OPEN
    failure_window_seconds: float = 60.0
    cooldown_seconds: float = 30.0    # OPEN → HALF_OPEN
    half_open_max_probes: int = 1     # only one probe at a time in HALF_OPEN


class CircuitBreaker:
    """Standard three-state breaker.

    Adapter usage:
        if not await breaker.allow(worker_id):
            return MelaResult.failure(... code="BREAKER_OPEN", retryable=True)
        try:
            result = await call_worker(...)
            await breaker.record_success(worker_id)
        except Exception:
            await breaker.record_failure(worker_id)
            raise
    """

    def __init__(
        self,
        store: BreakerStore,
        config: Optional[BreakerConfig] = None,
    ) -> None:
        self._store = store
        self._config = config or BreakerConfig()
        # Per-worker probe-in-flight counter (HALF_OPEN gate).
        self._probes: dict[str, int] = {}
        self._probe_lock = asyncio.Lock()

    async def allow(self, worker_id: str) -> bool:
        snap = await self._store.get(worker_id)
        now = time.monotonic()

        if snap.state == BreakerState.CLOSED:
            return True

        if snap.state == BreakerState.OPEN:
            if (
                snap.opened_at is not None
                and (now - snap.opened_at) >= self._config.cooldown_seconds
            ):
                # Cooldown elapsed — transition to HALF_OPEN and let one probe through.
                snap.state = BreakerState.HALF_OPEN
                await self._store.set(snap)
                _fire_breaker_alert(
                    code="BREAKER_HALF_OPEN",
                    severity="warning",
                    worker_id=worker_id,
                    message=f"Breaker entering HALF_OPEN — probing worker={worker_id}",
                )
                return await self._reserve_probe(worker_id)
            return False

        if snap.state == BreakerState.HALF_OPEN:
            return await self._reserve_probe(worker_id)

        return True  # unreachable, fail open rather than fail closed

    async def record_success(self, worker_id: str) -> None:
        snap = await self._store.get(worker_id)
        snap.last_success_at = time.monotonic()
        snap.state = BreakerState.CLOSED
        snap.failure_count = 0
        snap.opened_at = None
        snap.failure_window = []
        await self._store.set(snap)
        await self._release_probe(worker_id)

    async def record_failure(self, worker_id: str) -> None:
        snap = await self._store.get(worker_id)
        now = time.monotonic()
        cutoff = now - self._config.failure_window_seconds
        snap.failure_window = [t for t in snap.failure_window if t >= cutoff]
        snap.failure_window.append(now)
        snap.failure_count = len(snap.failure_window)
        snap.last_failure_at = now

        if snap.state == BreakerState.HALF_OPEN:
            snap.state = BreakerState.OPEN
            snap.opened_at = now
            _fire_breaker_alert(
                code="BREAKER_OPEN",
                severity="critical",
                worker_id=worker_id,
                message=f"Breaker tripped OPEN from HALF_OPEN — worker={worker_id} probe failed",
            )
        elif snap.state == BreakerState.CLOSED and (
            snap.failure_count >= self._config.failure_threshold
        ):
            snap.state = BreakerState.OPEN
            snap.opened_at = now
            logger.warning(
                "Circuit breaker tripped OPEN for worker=%s "
                "(%d failures in %ds window)",
                worker_id,
                snap.failure_count,
                int(self._config.failure_window_seconds),
            )
            _fire_breaker_alert(
                code="BREAKER_OPEN",
                severity="critical",
                worker_id=worker_id,
                message=(
                    f"Circuit breaker tripped OPEN for worker={worker_id} "
                    f"({snap.failure_count} failures in "
                    f"{int(self._config.failure_window_seconds)}s window)"
                ),
            )

        await self._store.set(snap)
        await self._release_probe(worker_id)

    async def snapshot(self, worker_id: str) -> BreakerSnapshot:
        return await self._store.get(worker_id)

    async def all_snapshots(self) -> list[BreakerSnapshot]:
        return await self._store.all()

    # ── Probe accounting (HALF_OPEN) ─────────────────────────────────────

    async def _reserve_probe(self, worker_id: str) -> bool:
        async with self._probe_lock:
            in_flight = self._probes.get(worker_id, 0)
            if in_flight >= self._config.half_open_max_probes:
                return False
            self._probes[worker_id] = in_flight + 1
            return True

    async def _release_probe(self, worker_id: str) -> None:
        async with self._probe_lock:
            in_flight = self._probes.get(worker_id, 0)
            if in_flight > 0:
                self._probes[worker_id] = in_flight - 1


# Module-level singletons — single store + breaker per process.
breaker_store: BreakerStore = InMemoryBreakerStore()
circuit_breaker = CircuitBreaker(breaker_store)
