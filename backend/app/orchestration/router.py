"""
Mela AI - Router.

Resolves a single ``MelaTask`` to the adapter that should execute it.
Validates that the worker is registered, the capability exists in its
manifest, and the circuit breaker permits the call before handing the
task to the adapter.

The router NEVER raises.  Every code path returns either a usable
``WorkerAdapter`` (caller proceeds with ``adapter.execute(task)``) or a
pre-built ``MelaResult.failure(...)`` (caller forwards as-is).  This
matches the orchestration brain's cardinal contract: a failing worker
produces a failed result, not an exception.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Optional, Union

from sqlalchemy.ext.asyncio import AsyncSession

from app.orchestration.adapters import WorkerAdapter, adapter_factory
from app.orchestration.adapters.factory import AdapterFactory
from app.orchestration.registry import WorkerRegistry, worker_registry
from app.orchestration.types import MelaResult, MelaTask

logger = logging.getLogger(__name__)


@dataclass
class RouteFailure:
    """Pre-built failure that the executor surfaces verbatim — never executed."""

    result: MelaResult


RouteOutcome = Union[WorkerAdapter, RouteFailure]


class Router:
    """Look up an adapter for a MelaTask, with capability validation.

    The circuit breaker is intentionally NOT consulted here — adapter
    base ``execute()`` is the single source of truth for breaker state.
    Checking again at the router level would double-burn HALF_OPEN
    probe slots and complicate the contract for callers who hold an
    adapter directly.
    """

    def __init__(
        self,
        registry: Optional[WorkerRegistry] = None,
        factory: Optional[AdapterFactory] = None,
    ) -> None:
        self._registry = registry or worker_registry
        self._factory = factory or adapter_factory

    async def route(self, db: AsyncSession, task: MelaTask) -> RouteOutcome:
        """Resolve the adapter for *task*.

        Returns:
            ``WorkerAdapter`` — caller calls ``adapter.execute(task)``.
            ``RouteFailure`` — caller forwards ``result`` as-is; do NOT
            attempt to execute.

        Failure codes:
            ``UNKNOWN_WORKER``        — worker_id not in the registry
            ``UNKNOWN_CAPABILITY``    — capability not declared by worker
            ``WORKER_ACCESS_DENIED``  — tenant lacks an access grant
                                         (Phase 5C — default-deny only)
            ``ADAPTER_UNAVAILABLE``   — registry has the worker but no
                                         adapter handles its protocol
        """
        manifest = await self._registry.get(db, task.worker_id)
        if manifest is None:
            return RouteFailure(
                MelaResult.failure(
                    task=task,
                    code="UNKNOWN_WORKER",
                    message=f"worker {task.worker_id!r} not registered",
                    retryable=False,
                    source="router",
                )
            )

        if not manifest.has_capability(task.capability):
            return RouteFailure(
                MelaResult.failure(
                    task=task,
                    code="UNKNOWN_CAPABILITY",
                    message=(
                        f"capability {task.capability!r} not declared "
                        f"by worker {task.worker_id!r}"
                    ),
                    retryable=False,
                    source="router",
                )
            )

        # Phase 5C: tenant access check.  Cheap no-op when
        # WORKER_ACCESS_DEFAULT_ALLOW is True (the default).  Defence
        # in depth — synth_worker_tools should already have stripped
        # this worker from the LLM's tool list, but a hand-crafted
        # MelaTask must not bypass the policy.
        from app.orchestration.access import has_access
        if not await has_access(
            db,
            worker_id=task.worker_id,
            tenant_id=task.context.tenant_id or None,
        ):
            return RouteFailure(
                MelaResult.failure(
                    task=task,
                    code="WORKER_ACCESS_DENIED",
                    message=(
                        f"tenant has no access grant for worker "
                        f"{task.worker_id!r}"
                    ),
                    retryable=False,
                    source="router",
                )
            )

        adapter = self._factory.get(manifest)
        if adapter is None:
            return RouteFailure(
                MelaResult.failure(
                    task=task,
                    code="ADAPTER_UNAVAILABLE",
                    message=(
                        f"no adapter for protocol "
                        f"{manifest.protocol.value!r} (worker={task.worker_id!r})"
                    ),
                    retryable=False,
                    source="router",
                )
            )

        return adapter


# Module-level singleton.
router = Router()
