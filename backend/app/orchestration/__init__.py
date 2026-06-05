"""
Mela AI - Orchestration Brain.

Cross-worker coordination layer. Sits ABOVE independent worker apps
(Mela Task Radar, future Meeting Assistant, etc.) and observes / commands /
aggregates / reasons across them.

Cardinal rule: workers never depend on Mela to function. Mela degrades
gracefully when any worker is down.

NOTE: this package is intentionally separate from
``app.services.outcome_orchestrator`` (single-LLM intent execution). The two
solve different problems and must not import from each other in Phase 1.
"""

from app.orchestration.types import (
    Capability,
    MelaContext,
    MelaError,
    MelaResult,
    MelaTask,
    Priority,
    Protocol,
    WorkerManifest,
    WorkerStatus,
)

__all__ = [
    "Capability",
    "MelaContext",
    "MelaError",
    "MelaResult",
    "MelaTask",
    "Priority",
    "Protocol",
    "WorkerManifest",
    "WorkerStatus",
]
