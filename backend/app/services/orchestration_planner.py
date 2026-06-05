"""
Mela AI - Orchestration Planner.

Decomposes a user goal into an :class:`ExecutionPlan` of MelaTasks
addressed to registered worker capabilities.  Phase 3 surface — the
LLM does the decomposition, the planner does the validation.

Hard rules (enforced in code, not in the prompt — the LLM is not
trusted to gate execution):

  1. If no workers are registered, return ``PlanningFailure(
     NO_WORKERS_REGISTERED)`` immediately.  Don't burn an LLM call.
  2. After parsing, every ``worker_id + capability`` pair is validated
     against the registry.  Unknown pairs are stripped and reported in
     ``warnings``.  Empty batches are removed.  Zero remaining batches
     → ``PlanningFailure(NO_VALID_TASKS)``.
  3. ``len(batches) > 10`` → ``PlanningFailure(PLAN_TOO_COMPLEX)``.  We
     refuse to execute unbounded plans.
  4. ``resolvable=false`` from the LLM → ``PlanningFailure`` with the
     LLM's reason.  The planner trusts "I can't" but never trusts an
     unbounded "yes I can".
  5. ``estimated_total_ms > 45000`` → attach ``slow_plan=True`` to the
     returned plan.  The caller decides whether to warn the user.

The planner calls ``openai_service.create_completion`` directly (not
``model_router.stream``) — it needs structured JSON output, not a
conversational stream.  If ``openai_service`` is unavailable, returns
``PlanningFailure(LLM_UNAVAILABLE)`` synchronously.
"""

from __future__ import annotations

import json
import logging
import re
import uuid
from dataclasses import dataclass, field
from typing import Any, Optional

from sqlalchemy.ext.asyncio import AsyncSession

from app.orchestration.executor import ExecutionPlan, TaskBatch
from app.orchestration.registry import WorkerRegistry, worker_registry
from app.orchestration.types import (
    MelaContext,
    MelaTask,
    Priority,
    WorkerManifest,
)

logger = logging.getLogger(__name__)


# Hard guards — used in tests too, kept as module constants.
MAX_BATCHES = 10
SLOW_PLAN_THRESHOLD_MS = 45_000


# ── Result types ─────────────────────────────────────────────────────────


@dataclass
class PlanningContext:
    """What the planner needs to mint a plan that respects user scope."""

    user_id: str
    tenant_id: Optional[str]
    profile_mode: str = "personal"
    project_id: Optional[str] = None
    priority: Priority = Priority.NORMAL


@dataclass
class PlanningFailure:
    """Returned when the planner can't (or won't) produce a plan."""

    reason: str
    detail: str = ""

    @property
    def resolvable(self) -> bool:
        return False


@dataclass
class AnnotatedPlan:
    """Wraps :class:`ExecutionPlan` with planner-side telemetry."""

    plan: ExecutionPlan
    estimated_total_ms: int = 0
    slow_plan: bool = False
    warnings: list[str] = field(default_factory=list)

    @property
    def resolvable(self) -> bool:
        return True


# ── Planner ──────────────────────────────────────────────────────────────


class OrchestrationPlanner:
    """LLM-driven decomposer of user goals into MelaTask graphs."""

    PRIMARY_MODEL = "gpt-4.1"
    FALLBACK_MODEL = "gpt-4o-mini"
    MAX_TOKENS = 1024

    def __init__(self, registry: Optional[WorkerRegistry] = None) -> None:
        self._registry = registry or worker_registry

    # ── Public API ───────────────────────────────────────────────────────

    async def plan(
        self,
        goal: str,
        context: PlanningContext,
        db: AsyncSession,
    ) -> AnnotatedPlan | PlanningFailure:
        """Decompose *goal* into an executable plan or report a failure."""
        manifests = await self._registry.list(db)
        if not manifests:
            return PlanningFailure(
                reason="NO_WORKERS_REGISTERED",
                detail="planner has no capability surface to choose from",
            )

        # Filter out enterprise-only workers in personal mode — same rule
        # the tool bridge enforces.  Personal users should never be
        # offered enterprise capabilities even by the planner.
        if context.profile_mode != "work":
            manifests = [
                m for m in manifests
                if (m.auth_config or {}).get("scope") != "enterprise"
            ]
            if not manifests:
                return PlanningFailure(
                    reason="NO_WORKERS_REGISTERED",
                    detail="no personal-scope workers registered",
                )

        try:
            from app.services.openai_service import openai_service
        except Exception:
            openai_service = None  # type: ignore[assignment]
        if openai_service is None:
            return PlanningFailure(
                reason="LLM_UNAVAILABLE",
                detail="openai_service is not configured",
            )

        sys_prompt = self._system_prompt(manifests)
        user_prompt = self._user_prompt(goal)

        plan_dict = await self._call_llm(
            openai_service, sys_prompt, user_prompt
        )
        if plan_dict is None:
            return PlanningFailure(
                reason="LLM_PARSE_ERROR",
                detail="planner LLM returned no parseable JSON",
            )

        # LLM-side resolvability — trust "no", never an unbounded "yes".
        if not plan_dict.get("resolvable", False):
            return PlanningFailure(
                reason="UNRESOLVABLE",
                detail=str(
                    plan_dict.get("unresolvable_reason")
                    or "planner reported the goal is unresolvable"
                ),
            )

        raw_batches = plan_dict.get("batches") or []
        if not isinstance(raw_batches, list):
            return PlanningFailure(
                reason="LLM_BAD_SHAPE",
                detail="planner JSON missing or malformed 'batches' list",
            )

        if len(raw_batches) > MAX_BATCHES:
            return PlanningFailure(
                reason="PLAN_TOO_COMPLEX",
                detail=(
                    f"planner returned {len(raw_batches)} batches, "
                    f"max is {MAX_BATCHES}"
                ),
            )

        # Validate every (worker_id, capability) pair against the registry.
        valid_workers = {m.id: m for m in manifests}
        validated_batches: list[TaskBatch] = []
        warnings: list[str] = []

        for idx, raw_batch in enumerate(raw_batches):
            raw_tasks = (raw_batch or {}).get("tasks") or []
            if not isinstance(raw_tasks, list):
                continue
            mtasks: list[MelaTask] = []
            for raw_task in raw_tasks:
                worker_id = (raw_task or {}).get("worker_id") or ""
                capability = (raw_task or {}).get("capability") or ""
                if worker_id not in valid_workers:
                    warnings.append(
                        f"stripped: unknown worker_id={worker_id!r}"
                    )
                    continue
                manifest = valid_workers[worker_id]
                if not manifest.has_capability(capability):
                    warnings.append(
                        f"stripped: capability={capability!r} not declared "
                        f"by worker={worker_id!r}"
                    )
                    continue
                # Coerce execution_mode + params into the canonical shape.
                exec_mode = raw_task.get("execution_mode")
                if exec_mode not in ("sync", "async"):
                    cap_meta = manifest.capability(capability)
                    exec_mode = (
                        "async"
                        if cap_meta and cap_meta.is_async
                        else "sync"
                    )
                params = raw_task.get("params") or {}
                if not isinstance(params, dict):
                    params = {}
                mtasks.append(
                    MelaTask(
                        capability=capability,
                        worker_id=worker_id,
                        params=params,
                        context=MelaContext(
                            tenant_id=context.tenant_id or "",
                            user_id=context.user_id,
                            project_id=context.project_id,
                            priority=context.priority,
                        ),
                        execution_mode=exec_mode,
                        timeout_ms=manifest.timeout_ms,
                    )
                )
            if mtasks:
                validated_batches.append(
                    TaskBatch(batch_index=idx, tasks=mtasks)
                )

        if not validated_batches:
            return PlanningFailure(
                reason="NO_VALID_TASKS",
                detail=(
                    "every task the planner emitted referenced a "
                    "capability not present in the registry; "
                    + "; ".join(warnings)
                ),
            )

        plan_id = str(uuid.uuid4())
        goal_id = str(uuid.uuid4())
        execution_plan = ExecutionPlan(
            plan_id=plan_id,
            goal_id=goal_id,
            goal=goal[:500],
            batches=validated_batches,
            user_id=context.user_id,
            tenant_id=context.tenant_id,
            profile_mode=context.profile_mode,
        )
        # Trust the LLM's estimate but cap it sanely — fall back to a
        # cheap sum of capability-level estimates when missing.
        estimated_total_ms = int(plan_dict.get("estimated_total_ms") or 0)
        if estimated_total_ms <= 0:
            estimated_total_ms = sum(
                (
                    valid_workers[t.worker_id]
                    .capability(t.capability)
                    .estimated_ms
                    if valid_workers[t.worker_id].capability(t.capability)
                    else 1000
                )
                for batch in validated_batches
                for t in batch.tasks
            )

        return AnnotatedPlan(
            plan=execution_plan,
            estimated_total_ms=estimated_total_ms,
            slow_plan=estimated_total_ms > SLOW_PLAN_THRESHOLD_MS,
            warnings=warnings,
        )

    # ── Prompt construction ──────────────────────────────────────────────

    @staticmethod
    def _system_prompt(manifests: list[WorkerManifest]) -> str:
        cap_rows: list[str] = []
        for m in manifests:
            for cap in m.capabilities:
                params_short = json.dumps(
                    cap.input_params.get("properties") or {},
                    default=str,
                )[:300]
                cap_rows.append(
                    f"- worker_id={m.id} capability={cap.name} "
                    f"async={'true' if cap.is_async else 'false'} "
                    f"description={cap.description!r} "
                    f"params={params_short}"
                )
        capabilities_block = "\n".join(cap_rows) if cap_rows else "(none)"

        return (
            "You are Mela's orchestration planner.  Decompose the user "
            "goal into the MINIMAL set of capability calls needed to "
            "answer it.  You may ONLY use capabilities listed below — "
            "never invent worker IDs or capability names.\n\n"
            "Capabilities available:\n"
            f"{capabilities_block}\n\n"
            "Rules:\n"
            " 1. Identify which tasks can run in parallel and put those "
            "in the same batch; sequence tasks that depend on previous "
            "results across batches.\n"
            " 2. If the goal cannot be served by the listed "
            "capabilities, set `resolvable: false` and explain why in "
            "`unresolvable_reason`.  Do NOT hallucinate.\n"
            " 3. Keep plans small.  Hard cap: 10 batches.  If the goal "
            "requires more, summarise and reduce.\n"
            " 4. Do NOT include `user_id` or `tenant_id` in `params` — "
            "the orchestrator overlays those from MelaContext.\n\n"
            "Return STRICT JSON matching this schema, no prose, no "
            "markdown fences:\n"
            "{\n"
            '  "resolvable": boolean,\n'
            '  "unresolvable_reason": string | null,\n'
            '  "estimated_total_ms": integer,\n'
            '  "batches": [\n'
            "    {\n"
            '      "batch_index": integer,\n'
            '      "tasks": [\n'
            "        {\n"
            '          "capability": string,\n'
            '          "worker_id": string,\n'
            '          "params": object,\n'
            '          "execution_mode": "sync" | "async",\n'
            '          "depends_on": []\n'
            "        }\n"
            "      ]\n"
            "    }\n"
            "  ]\n"
            "}"
        )

    @staticmethod
    def _user_prompt(goal: str) -> str:
        return f"User goal:\n{goal.strip()[:1500]}"

    # ── LLM call + parse ─────────────────────────────────────────────────

    async def _call_llm(
        self,
        openai_service: Any,
        system_prompt: str,
        user_prompt: str,
    ) -> Optional[dict[str, Any]]:
        """Two attempts (gpt-4.1 → gpt-4o-mini); return parsed dict or None."""
        for model in (self.PRIMARY_MODEL, self.FALLBACK_MODEL):
            try:
                raw = await openai_service.get_completion(
                    messages=[
                        {"role": "system", "content": system_prompt},
                        {"role": "user", "content": user_prompt},
                    ],
                    model=model,
                    max_tokens=self.MAX_TOKENS,
                    temperature=0.1,
                )
            except Exception as exc:  # noqa: BLE001 — never raise from planner
                logger.warning(
                    "planner LLM call failed model=%s err=%s", model, exc
                )
                continue
            if not raw:
                continue
            parsed = self._parse_json(raw)
            if parsed is not None:
                return parsed
            logger.warning(
                "planner LLM returned unparseable JSON model=%s body=%.200s",
                model, raw,
            )
        return None

    @staticmethod
    def _parse_json(raw: str) -> Optional[dict[str, Any]]:
        """Tolerant JSON parse — strips markdown fences if present."""
        text = raw.strip()
        # Strip ```json ... ``` fences if the model insists on them.
        if text.startswith("```"):
            fence = re.match(r"^```(?:json)?\s*(.*?)```$", text, re.DOTALL)
            if fence:
                text = fence.group(1).strip()
        try:
            obj = json.loads(text)
        except Exception:
            return None
        if not isinstance(obj, dict):
            return None
        return obj


# Module-level singleton — same pattern as other services.
orchestration_planner = OrchestrationPlanner()
