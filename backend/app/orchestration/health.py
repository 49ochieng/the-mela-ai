"""
Mela AI - Orchestration health summary.

Phase 1 scope: state-only.  This module reads from the registry and
the circuit breaker store and returns a structured snapshot.  It does
NOT yet issue live HTTP probes — the per-worker health-poller is a
Phase 3 concern.  This still gives Mela's overall health endpoint a
clean, accurate view of "what does Mela currently believe about each
worker" without network calls on the hot path.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.orchestration.breaker import (
    BreakerSnapshot,
    BreakerState,
    CircuitBreaker,
    circuit_breaker,
)
from app.orchestration.registry import WorkerRegistry, worker_registry
from app.orchestration.types import WorkerStatus


def _derive_effective_status(
    registered_status: WorkerStatus, breaker: BreakerSnapshot
) -> str:
    """Combine last-known registry status with current breaker state.

    UNCONFIGURED is sticky — admins need to see "not set up yet"
    distinctly from "configured but not responding" (UNREACHABLE), and
    the breaker has no opinion on something we never tried to call.
    Breaker tripped → unreachable, regardless of stale registry status.
    Breaker probing → degraded.
    Otherwise → fall back to the registry's last reported status.
    """
    if registered_status == WorkerStatus.UNCONFIGURED:
        return WorkerStatus.UNCONFIGURED.value
    if breaker.state == BreakerState.OPEN:
        return WorkerStatus.UNREACHABLE.value
    if breaker.state == BreakerState.HALF_OPEN:
        return WorkerStatus.DEGRADED.value
    return registered_status.value


async def get_worker_health_summary(
    db: AsyncSession,
    *,
    registry: WorkerRegistry | None = None,
    breaker: CircuitBreaker | None = None,
) -> dict[str, Any]:
    """Return a structured snapshot of every registered worker's health.

    Shape:
        {
          "generated_at": "2026-05-04T18:30:00Z",
          "worker_count": 1,
          "summary": {"healthy": 1, "degraded": 0, "unreachable": 0, "unknown": 0},
          "workers": [
            {
              "id": "task-radar",
              "display_name": "Mela Task Radar",
              "status": "healthy",            # effective status
              "registered_status": "healthy", # what the registry recorded
              "breaker": {
                  "state": "closed",
                  "failure_count": 0,
                  "opened_at": null,
                  "last_failure_at": null,
                  "last_success_at": 12345.67
              },
              "version": "1.0.0",
              "protocol": "mcp",
              "last_health_check": null
            },
            ...
          ]
        }
    """
    reg = registry or worker_registry
    brk = breaker or circuit_breaker

    manifests = await reg.list(db)

    counters = {
        "healthy": 0, "degraded": 0, "unreachable": 0,
        "unknown": 0, "unconfigured": 0,
    }
    workers: list[dict[str, Any]] = []

    for manifest in manifests:
        snap = await brk.snapshot(manifest.id)
        effective = _derive_effective_status(manifest.status, snap)
        counters[effective] = counters.get(effective, 0) + 1

        workers.append(
            {
                "id": manifest.id,
                "display_name": manifest.display_name,
                "version": manifest.version,
                "protocol": manifest.protocol.value,
                "status": effective,
                "registered_status": manifest.status.value,
                "last_health_check": (
                    manifest.last_health_check.isoformat()
                    if manifest.last_health_check
                    else None
                ),
                "breaker": {
                    "state": snap.state.value,
                    "failure_count": snap.failure_count,
                    "opened_at": snap.opened_at,
                    "last_failure_at": snap.last_failure_at,
                    "last_success_at": snap.last_success_at,
                },
            }
        )

    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "worker_count": len(workers),
        "summary": counters,
        "workers": workers,
    }
