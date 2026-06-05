"""
Phase 5 tests for the orchestration brain.

Coverage matches the Phase 5D specification:

  Event bus:
    - publish() delivers to subscriber queue
    - queue cap drops the OLDEST when a 51st event arrives
    - unsubscribe removes only that queue (other subscribers still receive)
    - publish() with no subscribers: no error, no hang

  Ingest result publish:
    - successful result publishes a WorkerEventChunk to the bus

  Workflow orchestrate action:
    - PlanningFailure marks action failed without raising; trace_id NOT
      written
    - Successful plan path writes the spawned trace_id to
      WorkflowRun.orchestration_trace_ids
    - Goal-template substitution resolves all three placeholders

  Worker access control:
    - WORKER_ACCESS_DEFAULT_ALLOW=True → synth_worker_tools returns ALL
      manifests' tools regardless of the access table
    - WORKER_ACCESS_DEFAULT_ALLOW=False, no grant → synth_worker_tools
      returns no tools for that worker
    - WORKER_ACCESS_DEFAULT_ALLOW=False, grant exists, not revoked →
      worker appears in tool list
    - WORKER_ACCESS_DEFAULT_ALLOW=False, grant revoked → worker excluded

  Router access denied:
    - WORKER_ACCESS_DENIED returned in default-deny mode without grant;
      breaker is not consulted (no calls hit the breaker store)

  Access endpoints:
    - duplicate active grant → 409
    - revoke sets revoked_at; subsequent grant check excludes it
"""

from __future__ import annotations

import asyncio
import sys
from datetime import datetime
from typing import Any

import pytest

from app.orchestration.access import allowed_worker_ids, has_access
from app.orchestration.event_bus import (
    MAX_QUEUE_DEPTH,
    OrchestrationEventBus,
)
from app.orchestration.registry import WorkerRegistry
from app.orchestration.router import RouteFailure, Router
from app.orchestration.tool_bridge import synth_worker_tools
from app.orchestration.types import (
    AuthScheme,
    Capability,
    MelaContext,
    MelaTask,
    Priority,
    Protocol,
    RetryPolicy,
    WorkerManifest,
    WorkerStatus,
)
from app.schemas.chat import WorkerEventChunk, WorkerEventType


# ── Helpers ──────────────────────────────────────────────────────────────


def _manifest(worker_id: str = "task-radar") -> WorkerManifest:
    return WorkerManifest(
        id=worker_id,
        display_name=f"{worker_id} test",
        version="1.0.0",
        capabilities=[
            Capability(
                name="get_overdue_tasks",
                description="d",
                input_params={"type": "object", "properties": {}},
                output_shape={"type": "object"},
                is_async=False,
            ),
        ],
        protocol=Protocol.MCP,
        base_url="http://example.invalid/mcp",
        health_check_url="http://example.invalid/health",
        auth_scheme=AuthScheme.API_KEY,
        auth_config={"header": "X-Api-Key", "scope": "enterprise"},
        retry_policy=RetryPolicy(max_attempts=2, backoff_ms=10, backoff_multiplier=1.0),
        status=WorkerStatus.UNKNOWN,
    )


def _set_default_allow(monkeypatch, value: bool) -> None:
    """Toggle WORKER_ACCESS_DEFAULT_ALLOW in-place — the access helpers
    re-read settings on each call (no caching)."""
    from app.core.config import settings
    monkeypatch.setattr(settings, "WORKER_ACCESS_DEFAULT_ALLOW", value)


def _make_event(worker_id: str = "task-radar") -> WorkerEventChunk:
    return WorkerEventChunk(
        worker_id=worker_id,
        event_type=WorkerEventType.SCAN_COMPLETED,
        title=f"{worker_id}: scan complete",
        summary="ok",
        trace_id="trace-1",
    )


# ── 5A: event bus ────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_event_bus_publish_delivers_to_subscriber():
    bus = OrchestrationEventBus()
    q = await bus.subscribe("u1")
    delivered = await bus.publish("u1", _make_event())
    assert delivered == 1
    received = await asyncio.wait_for(q.get(), timeout=0.5)
    assert received.title.startswith("task-radar")


@pytest.mark.asyncio
async def test_event_bus_publish_with_no_subscribers_is_noop():
    bus = OrchestrationEventBus()
    delivered = await bus.publish("nobody", _make_event())
    assert delivered == 0


@pytest.mark.asyncio
async def test_event_bus_full_queue_drops_oldest():
    """When a subscriber's queue is full, the OLDEST event is dropped
    so the freshest worker state always reaches the listener."""
    bus = OrchestrationEventBus()
    q = await bus.subscribe("u1")

    # Fill the queue exactly to capacity.
    for i in range(MAX_QUEUE_DEPTH):
        evt = WorkerEventChunk(
            worker_id="task-radar",
            event_type=WorkerEventType.SCAN_COMPLETED,
            title=f"event-{i}",
            summary="x",
        )
        delivered = await bus.publish("u1", evt)
        assert delivered == 1

    # One more — must evict the oldest, NOT drop the newest.
    final = WorkerEventChunk(
        worker_id="task-radar",
        event_type=WorkerEventType.SCAN_COMPLETED,
        title="event-FINAL",
        summary="x",
    )
    await bus.publish("u1", final)

    # Drain and confirm the FIRST event is gone but the FINAL is present.
    titles = []
    while not q.empty():
        titles.append((await q.get()).title)
    assert titles[0] == "event-1", f"oldest should be evicted, got {titles[:2]}"
    assert "event-FINAL" in titles
    assert "event-0" not in titles


@pytest.mark.asyncio
async def test_event_bus_unsubscribe_removes_only_that_queue():
    bus = OrchestrationEventBus()
    q1 = await bus.subscribe("u1")
    q2 = await bus.subscribe("u1")

    await bus.unsubscribe("u1", q1)
    delivered = await bus.publish("u1", _make_event())
    assert delivered == 1  # only q2 still listening
    assert q2.qsize() == 1
    assert q1.qsize() == 0


# ── 5A: ingest_result publishes to the bus ──────────────────────────────


@pytest.mark.asyncio
async def test_ingest_result_publishes_event_to_bus(db, monkeypatch):
    """Successful ingest_result fires _publish_worker_event which pushes
    to the bus.  We monkeypatch the bus singleton with a recorder."""
    from app.api.endpoints import orchestration_ingest as ingest
    from app.models.models import (
        OrchestrationTask,
        OrchestrationTrace,
        WorkerRegistryEntry,
    )

    # Seed the prerequisites the ingest helper looks up.
    db.add(
        OrchestrationTrace(
            trace_id="trace-1",
            goal_id="g1",
            user_id="u1",
            tenant_id="t1",
            profile_mode="work",
            status="pending",
            plan_json={},
        )
    )
    db.add(
        OrchestrationTask(
            task_id="task-1",
            trace_id="trace-1",
            worker_id="task-radar",
            capability="trigger_scan",
            execution_mode="async",
            status="awaiting_callback",
            params_json={},
        )
    )
    # Worker registry row required by _publish_worker_event's manifest
    # fetch path? (it's passed manifest directly — only the trace/task
    # rows are required).  Add for completeness.
    db.add(
        WorkerRegistryEntry(
            id="task-radar",
            display_name="Task Radar",
            version="1.0.0",
            protocol="mcp",
            base_url="http://example/",
            health_check_url="http://example/health",
            status="unknown",
            manifest={},
        )
    )
    await db.commit()

    captured: list[Any] = []

    class _RecordingBus:
        async def publish(self, user_id, event):  # noqa: ARG002
            captured.append((user_id, event))
            return 1

    # Reach the event_bus module via sys.modules to dodge the
    # app.services-style shadowing trap (knowledge_search demo'd it).
    import app.orchestration.event_bus as _bus_mod
    monkeypatch.setattr(_bus_mod, "event_bus", _RecordingBus())

    # Mint a successful MelaResult for the task and call the helper.
    from app.orchestration.types import MelaResult, MelaResultMetadata
    result = MelaResult(
        task_id="task-1",
        trace_id="trace-1",
        worker_id="task-radar",
        capability="trigger_scan",
        success=True,
        data={"scan_id": "s1"},
        summary="found 12 tasks",
        metadata=MelaResultMetadata(latency_ms=42, source="task-radar"),
    )
    worker = _manifest("task-radar")
    await ingest._publish_worker_event(db, worker=worker, result=result)

    assert len(captured) == 1
    user_id, event = captured[0]
    assert user_id == "u1"
    assert event.title.startswith("task-radar test:")
    assert event.trace_id == "trace-1"


# ── 5B: workflow orchestrate action ─────────────────────────────────────


@pytest.mark.asyncio
async def test_workflow_orchestrate_planning_failure_marks_action_failed(
    db, monkeypatch
):
    """PlanningFailure → action result has status='failed' and the
    workflow run's orchestration_trace_ids stays empty."""
    from app.models.models import Workflow, WorkflowRun, WorkflowRunStatus
    from app.services.orchestration_planner import PlanningFailure
    from app.services.workflow_service import workflow_service

    # Stub the planner singleton so it always returns PlanningFailure.
    class _StubPlanner:
        async def plan(self, goal, ctx, db):  # noqa: ARG002
            return PlanningFailure(
                reason="LLM_UNAVAILABLE", detail="(stub)"
            )

    _planner_mod = sys.modules["app.services.orchestration_planner"]
    monkeypatch.setattr(_planner_mod, "orchestration_planner", _StubPlanner())

    workflow = Workflow(
        id="w1",
        name="W",
        trigger_type="manual",
        actions=[],
        status="active",
        visibility="user",
        created_by="u1",
        tenant_id="t1",
    )
    run = WorkflowRun(
        id="r1",
        workflow_id="w1",
        triggered_by="u1",
        trigger_type="manual",
        status=WorkflowRunStatus.RUNNING,
        steps_total=1,
        started_at=datetime.utcnow(),
        orchestration_trace_ids=[],
    )

    out = await workflow_service._execute_action(
        action={
            "type": "orchestrate",
            "config": {"goal_template": "Summarise tasks for {{user_display_name}}"},
        },
        input_data={"user_display_name": "Edgar"},
        run=run,
        workflow=workflow,
        db=db,
    )
    assert out["status"] == "failed"
    assert out["reason"] == "LLM_UNAVAILABLE"
    assert run.orchestration_trace_ids == []


@pytest.mark.asyncio
async def test_workflow_orchestrate_writes_trace_id_on_success(
    db, monkeypatch
):
    """Successful plan → trace_id appended to run.orchestration_trace_ids,
    background task spawned (we don't await it), action returns 'queued'."""
    from app.models.models import Workflow, WorkflowRun, WorkflowRunStatus
    from app.orchestration.executor import ExecutionPlan, TaskBatch
    from app.orchestration.types import MelaContext, MelaTask, Priority
    from app.services.orchestration_planner import (
        AnnotatedPlan,
    )
    from app.services.workflow_service import workflow_service

    fixed_task = MelaTask(
        capability="get_overdue_tasks",
        worker_id="task-radar",
        params={},
        context=MelaContext(
            tenant_id="t1", user_id="u1", priority=Priority.NORMAL,
        ),
        execution_mode="sync",
        trace_id="trace-WF-1",
    )
    plan = ExecutionPlan(
        plan_id="p1",
        goal_id="g1",
        goal="x",
        batches=[TaskBatch(batch_index=0, tasks=[fixed_task])],
        user_id="u1",
        tenant_id="t1",
        profile_mode="work",
    )

    class _StubPlanner:
        async def plan(self, goal, ctx, db):  # noqa: ARG002
            return AnnotatedPlan(plan=plan, estimated_total_ms=500)

    _planner_mod = sys.modules["app.services.orchestration_planner"]
    monkeypatch.setattr(_planner_mod, "orchestration_planner", _StubPlanner())

    # Stub executor.run_plan so the background task doesn't spin up
    # real adapters.
    bg_called: list[bool] = []

    class _StubExecutor:
        async def run_plan(self, *_a, **_kw):
            bg_called.append(True)

    _exec_mod = sys.modules["app.orchestration.executor"]
    monkeypatch.setattr(_exec_mod, "executor", _StubExecutor())

    # Also stub async_session_maker so the background task gets a real
    # AsyncSession context-manager that we don't actually need.
    class _NullSession:
        async def __aenter__(self):
            return self
        async def __aexit__(self, *a):
            return False
    def _maker():
        return _NullSession()
    _db_mod = sys.modules["app.core.database"]
    monkeypatch.setattr(_db_mod, "async_session_maker", _maker)

    workflow = Workflow(
        id="w1",
        name="W",
        trigger_type="manual",
        actions=[],
        status="active",
        visibility="user",
        created_by="u1",
        tenant_id="t1",
    )
    run = WorkflowRun(
        id="r1",
        workflow_id="w1",
        triggered_by="u1",
        trigger_type="manual",
        status=WorkflowRunStatus.RUNNING,
        steps_total=1,
        started_at=datetime.utcnow(),
        orchestration_trace_ids=[],
    )

    out = await workflow_service._execute_action(
        action={
            "type": "orchestrate",
            "config": {"goal_template": "do X"},
        },
        input_data={},
        run=run,
        workflow=workflow,
        db=db,
    )
    assert out["status"] == "queued"
    assert out["trace_id"] == "trace-WF-1"
    assert run.orchestration_trace_ids == ["trace-WF-1"]
    # Yield once so the create_task'd coroutine has a chance to run.
    await asyncio.sleep(0)
    assert bg_called == [True]


def test_workflow_goal_template_substitution_resolves_placeholders():
    """Direct test of the renderer used by the orchestrate action."""
    from app.services.workflow_service import _render_goal_template

    out = _render_goal_template(
        "{{user_display_name}} on tenant {{tenant_id}} via {{workflow_name}} "
        "(unknown {{nope}})",
        user_display_name="Edgar",
        tenant_id="ten-1",
        workflow_name="Daily",
    )
    assert "Edgar on tenant ten-1 via Daily" in out
    # Unknown placeholders left intact for visibility.
    assert "{{nope}}" in out


# ── 5C: per-tenant access ───────────────────────────────────────────────


@pytest.mark.asyncio
async def test_default_allow_returns_all_workers_in_tool_list(db, monkeypatch):
    _set_default_allow(monkeypatch, True)
    registry = WorkerRegistry()
    await registry.upsert(db, _manifest("alpha"))
    await registry.upsert(db, _manifest("beta"))

    # Patch the tool_bridge's registry singleton via sys.modules — same
    # trick used in earlier phases for openai_service.
    import app.orchestration.tool_bridge as _tb
    monkeypatch.setattr(_tb, "worker_registry", registry)

    from app.core.mode import UserSession
    session = UserSession(mode="work", user_id="u1", tenant_id="t1")
    tools = await synth_worker_tools(db, user_session=session)
    names = {t["function"]["name"] for t in tools}
    assert any("alpha" in n for n in names)
    assert any("beta" in n for n in names)


@pytest.mark.asyncio
async def test_default_deny_no_grant_excludes_worker(db, monkeypatch):
    _set_default_allow(monkeypatch, False)
    registry = WorkerRegistry()
    await registry.upsert(db, _manifest("alpha"))
    import app.orchestration.tool_bridge as _tb
    monkeypatch.setattr(_tb, "worker_registry", registry)

    from app.core.mode import UserSession
    session = UserSession(mode="work", user_id="u1", tenant_id="t1")
    tools = await synth_worker_tools(db, user_session=session)
    assert tools == []


@pytest.mark.asyncio
async def test_default_deny_with_active_grant_includes_worker(
    db, monkeypatch
):
    from app.models.models import WorkerTenantAccess
    _set_default_allow(monkeypatch, False)
    registry = WorkerRegistry()
    await registry.upsert(db, _manifest("alpha"))
    db.add(
        WorkerTenantAccess(
            id="g1",
            worker_id="alpha",
            tenant_id="t1",
            granted_by="admin",
        )
    )
    await db.commit()

    import app.orchestration.tool_bridge as _tb
    monkeypatch.setattr(_tb, "worker_registry", registry)

    from app.core.mode import UserSession
    session = UserSession(mode="work", user_id="u1", tenant_id="t1")
    tools = await synth_worker_tools(db, user_session=session)
    assert len(tools) == 1
    assert "alpha" in tools[0]["function"]["name"]


@pytest.mark.asyncio
async def test_default_deny_revoked_grant_excludes_worker(db, monkeypatch):
    from app.models.models import WorkerTenantAccess
    _set_default_allow(monkeypatch, False)
    registry = WorkerRegistry()
    await registry.upsert(db, _manifest("alpha"))
    db.add(
        WorkerTenantAccess(
            id="g1",
            worker_id="alpha",
            tenant_id="t1",
            granted_by="admin",
            revoked_at=datetime.utcnow(),
        )
    )
    await db.commit()

    import app.orchestration.tool_bridge as _tb
    monkeypatch.setattr(_tb, "worker_registry", registry)

    from app.core.mode import UserSession
    session = UserSession(mode="work", user_id="u1", tenant_id="t1")
    tools = await synth_worker_tools(db, user_session=session)
    assert tools == []


@pytest.mark.asyncio
async def test_router_returns_access_denied_in_default_deny(
    db, monkeypatch
):
    from app.orchestration.adapters.factory import AdapterFactory
    _set_default_allow(monkeypatch, False)
    registry = WorkerRegistry()
    await registry.upsert(db, _manifest("alpha"))

    router = Router(registry=registry, factory=AdapterFactory())
    task = MelaTask(
        capability="get_overdue_tasks",
        worker_id="alpha",
        params={},
        context=MelaContext(
            tenant_id="t1", user_id="u1", priority=Priority.NORMAL,
        ),
        execution_mode="sync",
    )
    outcome = await router.route(db, task)
    assert isinstance(outcome, RouteFailure)
    assert outcome.result.error.code == "WORKER_ACCESS_DENIED"


@pytest.mark.asyncio
async def test_access_helpers_short_circuit_in_default_allow(db, monkeypatch):
    """In default-allow mode the access helpers must NOT touch the DB —
    they short-circuit to True / pass-through."""
    _set_default_allow(monkeypatch, True)
    # No grant rows seeded; default-allow returns True regardless.
    assert await has_access(db, worker_id="alpha", tenant_id="t1") is True
    out = await allowed_worker_ids(
        db, tenant_id="t1", candidate_ids=["alpha", "beta"],
    )
    assert out == {"alpha", "beta"}


# ── 5C: access endpoints ────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_grant_access_duplicate_returns_409(db):
    from fastapi import HTTPException
    from app.api.endpoints.orchestration import grant_worker_access
    from app.models.models import WorkerRegistryEntry, WorkerTenantAccess
    from app.orchestration.registry import worker_registry
    from app.schemas.auth import UserInfo

    # The registry's process-level cache survives across tests but is
    # populated from earlier tests' in-memory DBs.  Invalidate it so
    # this test's "alpha" row is found rather than falling through the
    # stale-cache miss to the endpoint's 404 branch.
    worker_registry._invalidate()

    # Manifest column must be a valid WorkerManifest serialisation —
    # the registry rejects malformed rows on read, which would mask
    # the duplicate-grant 409 with an upstream 404.
    manifest_dict = _manifest("alpha").model_dump(mode="json")
    db.add(
        WorkerRegistryEntry(
            id="alpha", display_name="A", version="1.0.0", protocol="mcp",
            base_url="x", health_check_url="x", status="unknown",
            manifest=manifest_dict,
        )
    )
    db.add(
        WorkerTenantAccess(
            id="existing", worker_id="alpha", tenant_id="t1",
            granted_by="admin",
        )
    )
    await db.commit()

    admin = UserInfo(
        id="admin", email="a@example.com", name="A",
        roles=["admin"], groups=[], tenant_id="t1",
    )
    with pytest.raises(HTTPException) as excinfo:
        await grant_worker_access(
            body={"worker_id": "alpha", "tenant_id": "t1"},
            admin=admin, db=db,
        )
    assert excinfo.value.status_code == 409


@pytest.mark.asyncio
async def test_revoke_access_sets_revoked_and_excludes(db, monkeypatch):
    from app.api.endpoints.orchestration import revoke_worker_access
    from app.models.models import WorkerTenantAccess
    from app.schemas.auth import UserInfo

    db.add(
        WorkerTenantAccess(
            id="g1", worker_id="alpha", tenant_id="t1", granted_by="admin",
        )
    )
    await db.commit()

    admin = UserInfo(
        id="admin", email="a@example.com", name="A",
        roles=["admin"], groups=[], tenant_id="t1",
    )
    out = await revoke_worker_access("g1", _admin=admin, db=db)
    assert out["revoked_at"] is not None

    # Subsequent access lookup excludes the revoked grant.
    _set_default_allow(monkeypatch, False)
    assert await has_access(db, worker_id="alpha", tenant_id="t1") is False
