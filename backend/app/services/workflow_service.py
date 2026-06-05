"""
Mela AI - Workflow Automation Service

Workflows are trigger-action pipelines. A workflow has:
  - trigger_type: manual | schedule | keyword | event
  - trigger_config: dict with type-specific config (cron expression, keywords, event name, etc.)
  - actions: list of {type, config} dicts (send_message, run_skill, notify, etc.)

Execution is lightweight — runs are recorded synchronously for now.
Background scheduling is out of scope for v1 (trigger via API or frontend).
"""

import asyncio
import logging
import re
import uuid
from datetime import datetime
from typing import Any, Optional

from sqlalchemy import select

logger = logging.getLogger(__name__)


# Phase 5B: goal-template substitution.  Supports the three documented
# placeholders only — never silently swallows unknown placeholders so
# admins notice template typos early.
_TEMPLATE_PLACEHOLDER = re.compile(r"\{\{\s*([a-zA-Z_]\w*)\s*\}\}")


def _render_goal_template(
    template: str,
    *,
    user_display_name: str,
    tenant_id: str,
    workflow_name: str,
) -> str:
    """Resolve {{user_display_name}}, {{tenant_id}}, {{workflow_name}}
    in *template*.  Unknown placeholders are left intact so they're
    visible in the rendered goal."""
    values = {
        "user_display_name": user_display_name,
        "tenant_id": tenant_id,
        "workflow_name": workflow_name,
    }

    def _repl(m: re.Match) -> str:
        key = m.group(1)
        return values.get(key, m.group(0))

    return _TEMPLATE_PLACEHOLDER.sub(_repl, template)

# ── Built-in workflow templates ────────────────────────────────────────────────

WORKFLOW_TEMPLATES = [
    {
        "name": "Daily Standup Summary",
        "description": "Every morning, generate a structured standup prompt.",
        "trigger_type": "schedule",
        "trigger_config": {"cron": "0 9 * * 1-5", "timezone": "UTC"},
        "actions": [
            {
                "type": "send_message",
                "config": {
                    "template": "Generate a standup update template with sections: What I did yesterday, What I plan today, Blockers.",
                    "skill": "Executive Summary",
                },
            }
        ],
    },
    {
        "name": "Keyword Alert: Legal / Compliance",
        "description": "When a message contains compliance keywords, auto-inject compliance skill instructions.",
        "trigger_type": "keyword",
        "trigger_config": {"keywords": ["gdpr", "compliance", "legal", "regulation", "audit"]},
        "actions": [
            {
                "type": "inject_skill",
                "config": {"skill_name": "Compliance & Policy"},
            }
        ],
    },
    {
        "name": "Weekly Usage Report",
        "description": "Send a weekly summary of token usage and top topics.",
        "trigger_type": "schedule",
        "trigger_config": {"cron": "0 8 * * 1", "timezone": "UTC"},
        "actions": [
            {
                "type": "admin_report",
                "config": {"report_type": "weekly_usage", "recipients": "admins"},
            }
        ],
    },
]


class WorkflowService:

    async def list_workflows(
        self,
        db,
        user_id: str,
        tenant_id: Optional[str] = None,
        admin: bool = False,
    ) -> list:
        from app.models.models import Workflow
        from sqlalchemy import or_, and_

        try:
            q = select(Workflow).order_by(Workflow.updated_at.desc())
            if not admin:
                q = q.where(
                    or_(
                        Workflow.visibility == "global",
                        and_(Workflow.visibility == "org", Workflow.tenant_id == tenant_id) if tenant_id else False,
                        Workflow.created_by == user_id,
                    )
                )
            result = await db.execute(q)
            return result.scalars().all()
        except Exception as e:
            logger.warning("Failed to list workflows: %s", e)
            return []

    async def get_workflow(self, db, workflow_id: str) -> Optional[object]:
        from app.models.models import Workflow
        try:
            result = await db.execute(select(Workflow).where(Workflow.id == workflow_id))
            return result.scalar_one_or_none()
        except Exception as e:
            logger.warning("Failed to get workflow %s: %s", workflow_id, e)
            return None

    async def create_workflow(self, db, user_id: str, **kwargs) -> Optional[object]:
        from app.models.models import Workflow
        try:
            workflow = Workflow(
                id=str(uuid.uuid4()),
                name=kwargs["name"],
                description=kwargs.get("description"),
                trigger_type=kwargs.get("trigger_type", "manual"),
                trigger_config=kwargs.get("trigger_config"),
                actions=kwargs.get("actions", []),
                status=kwargs.get("status", "draft"),
                visibility=kwargs.get("visibility", "user"),
                created_by=user_id,
                user_id=user_id if kwargs.get("visibility", "user") == "user" else None,
                tenant_id=kwargs.get("tenant_id"),
            )
            db.add(workflow)
            await db.commit()
            await db.refresh(workflow)
            return workflow
        except Exception as e:
            logger.warning("Failed to create workflow: %s", e)
            return None

    async def update_workflow(self, db, workflow_id: str, user_id: str, admin: bool = False, **kwargs) -> Optional[object]:
        from app.models.models import Workflow
        try:
            result = await db.execute(select(Workflow).where(Workflow.id == workflow_id))
            workflow = result.scalar_one_or_none()
            if not workflow:
                return None
            if not admin and workflow.created_by != user_id:
                return None
            for k, v in kwargs.items():
                if hasattr(workflow, k) and v is not None:
                    setattr(workflow, k, v)
            workflow.updated_at = datetime.utcnow()
            await db.commit()
            await db.refresh(workflow)
            return workflow
        except Exception as e:
            logger.warning("Failed to update workflow %s: %s", workflow_id, e)
            return None

    async def delete_workflow(self, db, workflow_id: str, user_id: str, admin: bool = False) -> bool:
        from app.models.models import Workflow
        try:
            result = await db.execute(select(Workflow).where(Workflow.id == workflow_id))
            workflow = result.scalar_one_or_none()
            if not workflow:
                return False
            if not admin and workflow.created_by != user_id:
                return False
            await db.delete(workflow)
            await db.commit()
            return True
        except Exception as e:
            logger.warning("Failed to delete workflow %s: %s", workflow_id, e)
            return False

    async def run_workflow(
        self,
        db,
        workflow_id: str,
        triggered_by: str,
        input_data: Optional[dict] = None,
    ) -> Optional[object]:
        """Record and execute a workflow run (synchronous v1 — async scheduling in v2)."""
        from app.models.models import Workflow, WorkflowRun, WorkflowRunStatus

        try:
            result = await db.execute(select(Workflow).where(Workflow.id == workflow_id))
            workflow = result.scalar_one_or_none()
            if not workflow:
                return None

            actions = workflow.actions or []
            run = WorkflowRun(
                id=str(uuid.uuid4()),
                workflow_id=workflow_id,
                triggered_by=triggered_by,
                trigger_type="manual",
                status=WorkflowRunStatus.RUNNING,
                input_data=input_data or {},
                steps_total=len(actions),
                started_at=datetime.utcnow(),
                orchestration_trace_ids=[],
            )
            db.add(run)

            # Execute actions (v1: in-process simulation)
            output = {}
            try:
                for i, action in enumerate(actions):
                    output[f"step_{i + 1}"] = await self._execute_action(
                        action, input_data or {},
                        run=run, workflow=workflow, db=db,
                    )
                    run.steps_completed = i + 1

                run.status = WorkflowRunStatus.COMPLETED
                run.output_data = output
                run.finished_at = datetime.utcnow()

                # Update workflow counters
                workflow.run_count = (workflow.run_count or 0) + 1
                workflow.last_run_at = datetime.utcnow()

            except Exception as action_err:
                run.status = WorkflowRunStatus.FAILED
                run.error_message = str(action_err)
                run.finished_at = datetime.utcnow()

            await db.commit()
            await db.refresh(run)
            return run

        except Exception as e:
            logger.warning("Failed to run workflow %s: %s", workflow_id, e)
            return None

    async def _execute_action(
        self,
        action: dict,
        input_data: dict,
        *,
        run: Any = None,
        workflow: Any = None,
        db: Any = None,
    ) -> dict:
        """Execute a single workflow action. v1: simulation / stub.

        Phase 5B added the optional ``run`` / ``workflow`` / ``db``
        kwargs so the new ``orchestrate`` action can resolve user/tenant
        context and write the spawned trace_id back to the run.  Existing
        action types ignore these kwargs and behave identically.
        """
        action_type = action.get("type", "unknown")
        config = action.get("config", {})

        if action_type == "send_message":
            return {"type": action_type, "status": "queued", "template": config.get("template", "")}
        elif action_type == "inject_skill":
            return {"type": action_type, "status": "ok", "skill": config.get("skill_name", "")}
        elif action_type == "admin_report":
            return {"type": action_type, "status": "ok", "report_type": config.get("report_type", "")}
        elif action_type == "notify":
            return {"type": action_type, "status": "ok", "channel": config.get("channel", "email")}
        elif action_type == "orchestrate":
            return await self._execute_orchestrate(
                config=config,
                run=run,
                workflow=workflow,
                db=db,
                input_data=input_data,
            )
        else:
            return {"type": action_type, "status": "skipped", "reason": "unknown action type"}

    # ── Phase 5B: orchestrate action ─────────────────────────────────────

    async def _execute_orchestrate(
        self,
        *,
        config: dict,
        run: Any,
        workflow: Any,
        db: Any,
        input_data: dict,
    ) -> dict:
        """Run an orchestration plan as a workflow action.

        Hard rules:
          1. Always background — the workflow action returns the moment
             the planner produces an ExecutionPlan.  ``run_plan`` is
             dispatched with ``asyncio.create_task`` against a fresh
             session.  We never await plan completion inside the
             workflow run.
          2. PlanningFailure → action marked failed in the result dict
             (NOT raised).  Workflow-level retry is intentionally absent
             — orchestration handles its own retries via the breaker /
             adapter retry policy.
          3. The spawned trace_id is appended to ``run.orchestration_trace_ids``
             so admins can correlate.
        """
        if run is None or workflow is None or db is None:
            return {
                "type": "orchestrate",
                "status": "failed",
                "reason": "missing run/workflow/db context",
            }

        goal_template = (config or {}).get("goal_template") or ""
        if not goal_template.strip():
            return {
                "type": "orchestrate",
                "status": "failed",
                "reason": "goal_template is required",
            }

        user_id = run.triggered_by or "system"
        tenant_id = getattr(workflow, "tenant_id", None)
        user_display_name = (input_data or {}).get(
            "user_display_name", user_id
        )

        goal = _render_goal_template(
            goal_template,
            user_display_name=user_display_name,
            tenant_id=tenant_id or "",
            workflow_name=getattr(workflow, "name", "") or "",
        )

        try:
            from app.orchestration.executor import executor
            from app.orchestration.types import Priority
            from app.services.orchestration_planner import (
                AnnotatedPlan,
                PlanningContext,
                PlanningFailure,
                orchestration_planner,
            )
        except Exception as exc:  # noqa: BLE001 — orchestration optional
            return {
                "type": "orchestrate",
                "status": "failed",
                "reason": f"orchestration unavailable: {exc}",
            }

        ctx = PlanningContext(
            user_id=str(user_id),
            tenant_id=tenant_id,
            profile_mode="work" if tenant_id else "personal",
            priority=Priority.NORMAL,
        )
        outcome = await orchestration_planner.plan(goal, ctx, db)
        if isinstance(outcome, PlanningFailure):
            return {
                "type": "orchestrate",
                "status": "failed",
                "reason": outcome.reason,
                "detail": outcome.detail,
            }

        plan: "AnnotatedPlan" = outcome  # type: ignore[name-defined]

        # Optional worker_ids allowlist — strip tasks targeting workers
        # outside the list.  Empty list (or absent key) → no filter.
        allowlist = (config or {}).get("worker_ids") or []
        if allowlist:
            allow = set(allowlist)
            new_batches = []
            for b in plan.plan.batches:
                kept = [t for t in b.tasks if t.worker_id in allow]
                if kept:
                    b.tasks = kept
                    new_batches.append(b)
            plan.plan.batches = new_batches
            if not plan.plan.batches:
                return {
                    "type": "orchestrate",
                    "status": "failed",
                    "reason": "every planned task was outside worker_ids allowlist",
                }

        # Capture the trace_id from the first task (the executor will
        # normalise every task in the plan onto this id).  Recording it
        # NOW means the workflow action returns immediately even though
        # the plan executes asynchronously.
        trace_id = next(
            (t.trace_id for batch in plan.plan.batches for t in batch.tasks),
            None,
        )
        if trace_id:
            existing = list(run.orchestration_trace_ids or [])
            if trace_id not in existing:
                existing.append(trace_id)
            run.orchestration_trace_ids = existing

        # Background dispatch — fresh session so the workflow's session
        # commit doesn't fight the executor's writes.  Failures inside
        # the task are persisted on the OrchestrationTrace row, not
        # propagated up.
        async def _bg() -> None:
            try:
                from app.core.database import async_session_maker
                async with async_session_maker() as bg_db:
                    await executor.run_plan(bg_db, plan.plan)
            except Exception as exc:  # noqa: BLE001
                logger.warning(
                    "orchestrate background run failed trace=%s err=%s",
                    trace_id, exc,
                )

        asyncio.create_task(_bg())

        return {
            "type": "orchestrate",
            "status": "queued",
            "trace_id": trace_id,
            "estimated_total_ms": plan.estimated_total_ms,
            "slow_plan": plan.slow_plan,
            "warnings": plan.warnings,
            "goal": goal,
        }

    async def list_runs(self, db, workflow_id: str, limit: int = 20) -> list:
        from app.models.models import WorkflowRun
        try:
            result = await db.execute(
                select(WorkflowRun)
                .where(WorkflowRun.workflow_id == workflow_id)
                .order_by(WorkflowRun.created_at.desc())
                .limit(limit)
            )
            return result.scalars().all()
        except Exception as e:
            logger.warning("Failed to list runs for workflow %s: %s", workflow_id, e)
            return []


workflow_service = WorkflowService()
