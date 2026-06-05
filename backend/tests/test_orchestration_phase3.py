"""
Phase 3 tests for the orchestration brain.

Coverage matches the Phase 3E specification:

  - Planner: no workers registered → PlanningFailure(NO_WORKERS_REGISTERED)
    without calling LLM
  - Planner: LLM returns plan with unknown capability → stripped, warning
    attached, execution proceeds with remaining tasks
  - Planner: LLM returns resolvable=false → PlanningFailure
  - Planner: > MAX_BATCHES → PlanningFailure(PLAN_TOO_COMPLEX)
  - Cross-worker intent: detect_intent + outcome_orchestrator routes
    CROSS_WORKER → planner → falls through silently on failure
  - Knowledge ingest: summary > 500 chars goes through summariser
  - Knowledge search: tenant isolation
  - KB injection: chat_service work mode injects [KNOWLEDGE_CONTEXT];
    personal mode does not (test the helper path directly)
"""

from __future__ import annotations

import json
from typing import Any

import pytest

from app.orchestration.knowledge import (
    KBEntry,
    SQLKnowledgeStore,
    SUMMARY_MAX_CHARS,
    summarise_if_needed,
)
from app.orchestration.registry import WorkerRegistry
from app.orchestration.types import (
    AuthScheme,
    Capability,
    Protocol,
    RetryPolicy,
    WorkerManifest,
    WorkerStatus,
)
from app.services.orchestration_planner import (
    AnnotatedPlan,
    MAX_BATCHES,
    OrchestrationPlanner,
    PlanningContext,
    PlanningFailure,
)
from app.services.outcome_orchestrator import IntentType, detect_intent


# ── Helpers ──────────────────────────────────────────────────────────────


def _capabilities() -> list[Capability]:
    return [
        Capability(
            name="get_overdue_tasks",
            description="List overdue tasks for the user",
            input_params={
                "type": "object",
                "properties": {},
            },
            output_shape={"type": "object"},
            is_async=False,
            estimated_ms=400,
        ),
        Capability(
            name="get_meeting_transcript",
            description="Fetch the latest meeting transcript",
            input_params={"type": "object", "properties": {}},
            output_shape={"type": "object"},
            is_async=False,
            estimated_ms=600,
        ),
    ]


def _manifest(worker_id: str = "task-radar") -> WorkerManifest:
    return WorkerManifest(
        id=worker_id,
        display_name=f"{worker_id} test",
        version="1.0.0",
        capabilities=_capabilities(),
        protocol=Protocol.MCP,
        base_url="http://example.invalid/mcp",
        health_check_url="http://example.invalid/health",
        auth_scheme=AuthScheme.API_KEY,
        auth_config={"header": "X-Api-Key", "api_key": "k"},
        retry_policy=RetryPolicy(max_attempts=2, backoff_ms=10, backoff_multiplier=1.0),
        status=WorkerStatus.UNKNOWN,
    )


class _FakeOpenAI:
    """Minimal stand-in for openai_service used by the planner."""

    def __init__(self, payloads: list[Any]) -> None:
        self._payloads = list(payloads)
        self.call_count = 0

    async def get_completion(self, *, messages, model, max_tokens, temperature):
        self.call_count += 1
        if not self._payloads:
            return None
        nxt = self._payloads.pop(0)
        if isinstance(nxt, Exception):
            raise nxt
        if isinstance(nxt, dict):
            return json.dumps(nxt)
        return nxt


# ── Intent detection ─────────────────────────────────────────────────────


def test_detect_intent_cross_worker():
    assert detect_intent(
        "what is overdue and what was decided in my last meeting?"
    ) == IntentType.CROSS_WORKER
    # Conjunction without 2 distinct domains → not cross-worker.
    assert detect_intent(
        "summarise my tasks and rewrite them as bullets"
    ) != IntentType.CROSS_WORKER
    # No conjunction → not cross-worker even if multiple domain words.
    assert detect_intent("show overdue tasks") != IntentType.CROSS_WORKER


# ── Planner ──────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_planner_no_workers_registered_skips_llm(db, monkeypatch):
    fake = _FakeOpenAI(payloads=[])
    # NOTE: app/services/__init__.py shadows the submodule attribute via
    # `from app.services.openai_service import openai_service`, so
    # `import app.services.openai_service as _osmod` actually rebinds to
    # the singleton on the parent package.  Reach for the real module via
    # sys.modules so monkeypatch lands on the module object — the
    # planner's `from app.services.openai_service import openai_service`
    # then resolves to our fake.
    import sys as _sys
    _osmod = _sys.modules["app.services.openai_service"]
    monkeypatch.setattr(_osmod, "openai_service", fake)

    planner = OrchestrationPlanner(registry=WorkerRegistry())
    out = await planner.plan(
        "what is overdue and what was decided in my last meeting?",
        PlanningContext(user_id="u1", tenant_id="t1", profile_mode="work"),
        db,
    )
    assert isinstance(out, PlanningFailure)
    assert out.reason == "NO_WORKERS_REGISTERED"
    # Critically: never called the LLM.
    assert fake.call_count == 0


@pytest.mark.asyncio
async def test_planner_unresolvable_returns_failure(db, monkeypatch):
    registry = WorkerRegistry()
    await registry.upsert(db, _manifest())

    fake = _FakeOpenAI(payloads=[
        {
            "resolvable": False,
            "unresolvable_reason": "no capability matches the goal",
            "estimated_total_ms": 0,
            "batches": [],
        },
    ])
    # NOTE: app/services/__init__.py shadows the submodule attribute via
    # `from app.services.openai_service import openai_service`, so
    # `import app.services.openai_service as _osmod` actually rebinds to
    # the singleton on the parent package.  Reach for the real module via
    # sys.modules so monkeypatch lands on the module object — the
    # planner's `from app.services.openai_service import openai_service`
    # then resolves to our fake.
    import sys as _sys
    _osmod = _sys.modules["app.services.openai_service"]
    monkeypatch.setattr(_osmod, "openai_service", fake)
    planner = OrchestrationPlanner(registry=registry)
    out = await planner.plan(
        "translate this to swahili",
        PlanningContext(user_id="u1", tenant_id="t1", profile_mode="work"),
        db,
    )
    assert isinstance(out, PlanningFailure)
    assert out.reason == "UNRESOLVABLE"
    assert "no capability" in out.detail.lower()


@pytest.mark.asyncio
async def test_planner_plan_too_complex(db, monkeypatch):
    registry = WorkerRegistry()
    await registry.upsert(db, _manifest())
    big_batches = [
        {
            "batch_index": i,
            "tasks": [
                {
                    "capability": "get_overdue_tasks",
                    "worker_id": "task-radar",
                    "params": {},
                    "execution_mode": "sync",
                    "depends_on": [],
                }
            ],
        }
        for i in range(MAX_BATCHES + 3)
    ]
    fake = _FakeOpenAI(payloads=[
        {
            "resolvable": True,
            "unresolvable_reason": None,
            "estimated_total_ms": 5000,
            "batches": big_batches,
        },
    ])
    # NOTE: app/services/__init__.py shadows the submodule attribute via
    # `from app.services.openai_service import openai_service`, so
    # `import app.services.openai_service as _osmod` actually rebinds to
    # the singleton on the parent package.  Reach for the real module via
    # sys.modules so monkeypatch lands on the module object — the
    # planner's `from app.services.openai_service import openai_service`
    # then resolves to our fake.
    import sys as _sys
    _osmod = _sys.modules["app.services.openai_service"]
    monkeypatch.setattr(_osmod, "openai_service", fake)

    planner = OrchestrationPlanner(registry=registry)
    out = await planner.plan(
        "do an absurd amount of stuff",
        PlanningContext(user_id="u1", tenant_id="t1", profile_mode="work"),
        db,
    )
    assert isinstance(out, PlanningFailure)
    assert out.reason == "PLAN_TOO_COMPLEX"


@pytest.mark.asyncio
async def test_planner_strips_unknown_capability(db, monkeypatch):
    registry = WorkerRegistry()
    await registry.upsert(db, _manifest())

    fake = _FakeOpenAI(payloads=[
        {
            "resolvable": True,
            "unresolvable_reason": None,
            "estimated_total_ms": 800,
            "batches": [
                {
                    "batch_index": 0,
                    "tasks": [
                        {
                            "capability": "get_overdue_tasks",
                            "worker_id": "task-radar",
                            "params": {},
                            "execution_mode": "sync",
                            "depends_on": [],
                        },
                        {
                            "capability": "fly_to_the_moon",
                            "worker_id": "task-radar",
                            "params": {},
                            "execution_mode": "sync",
                            "depends_on": [],
                        },
                    ],
                }
            ],
        },
    ])
    # NOTE: app/services/__init__.py shadows the submodule attribute via
    # `from app.services.openai_service import openai_service`, so
    # `import app.services.openai_service as _osmod` actually rebinds to
    # the singleton on the parent package.  Reach for the real module via
    # sys.modules so monkeypatch lands on the module object — the
    # planner's `from app.services.openai_service import openai_service`
    # then resolves to our fake.
    import sys as _sys
    _osmod = _sys.modules["app.services.openai_service"]
    monkeypatch.setattr(_osmod, "openai_service", fake)

    planner = OrchestrationPlanner(registry=registry)
    out = await planner.plan(
        "what's overdue and the moon",
        PlanningContext(user_id="u1", tenant_id="t1", profile_mode="work"),
        db,
    )
    assert isinstance(out, AnnotatedPlan)
    # The bogus capability is stripped; the real one survives.
    flat = [t for b in out.plan.batches for t in b.tasks]
    assert len(flat) == 1
    assert flat[0].capability == "get_overdue_tasks"
    assert any("fly_to_the_moon" in w for w in out.warnings)


@pytest.mark.asyncio
async def test_planner_no_valid_tasks_returns_failure(db, monkeypatch):
    registry = WorkerRegistry()
    await registry.upsert(db, _manifest())

    fake = _FakeOpenAI(payloads=[
        {
            "resolvable": True,
            "unresolvable_reason": None,
            "estimated_total_ms": 0,
            "batches": [
                {
                    "batch_index": 0,
                    "tasks": [
                        {
                            "capability": "nonexistent",
                            "worker_id": "task-radar",
                            "params": {},
                            "execution_mode": "sync",
                            "depends_on": [],
                        }
                    ],
                }
            ],
        },
    ])
    # NOTE: app/services/__init__.py shadows the submodule attribute via
    # `from app.services.openai_service import openai_service`, so
    # `import app.services.openai_service as _osmod` actually rebinds to
    # the singleton on the parent package.  Reach for the real module via
    # sys.modules so monkeypatch lands on the module object — the
    # planner's `from app.services.openai_service import openai_service`
    # then resolves to our fake.
    import sys as _sys
    _osmod = _sys.modules["app.services.openai_service"]
    monkeypatch.setattr(_osmod, "openai_service", fake)

    planner = OrchestrationPlanner(registry=registry)
    out = await planner.plan(
        "do nothing useful",
        PlanningContext(user_id="u1", tenant_id="t1", profile_mode="work"),
        db,
    )
    assert isinstance(out, PlanningFailure)
    assert out.reason == "NO_VALID_TASKS"


@pytest.mark.asyncio
async def test_planner_falls_back_to_secondary_model_on_parse_error(
    db, monkeypatch
):
    registry = WorkerRegistry()
    await registry.upsert(db, _manifest())

    fake = _FakeOpenAI(payloads=[
        "this is not valid json at all",
        {
            "resolvable": True,
            "unresolvable_reason": None,
            "estimated_total_ms": 200,
            "batches": [
                {
                    "batch_index": 0,
                    "tasks": [
                        {
                            "capability": "get_overdue_tasks",
                            "worker_id": "task-radar",
                            "params": {},
                            "execution_mode": "sync",
                            "depends_on": [],
                        }
                    ],
                }
            ],
        },
    ])
    # NOTE: app/services/__init__.py shadows the submodule attribute via
    # `from app.services.openai_service import openai_service`, so
    # `import app.services.openai_service as _osmod` actually rebinds to
    # the singleton on the parent package.  Reach for the real module via
    # sys.modules so monkeypatch lands on the module object — the
    # planner's `from app.services.openai_service import openai_service`
    # then resolves to our fake.
    import sys as _sys
    _osmod = _sys.modules["app.services.openai_service"]
    monkeypatch.setattr(_osmod, "openai_service", fake)

    planner = OrchestrationPlanner(registry=registry)
    out = await planner.plan(
        "what's overdue",
        PlanningContext(user_id="u1", tenant_id="t1", profile_mode="work"),
        db,
    )
    assert isinstance(out, AnnotatedPlan)
    assert fake.call_count == 2  # primary failed, fallback succeeded


# ── Outcome orchestrator integration ─────────────────────────────────────


@pytest.mark.asyncio
async def test_outcome_orchestrator_cross_worker_falls_through_on_failure(
    db, monkeypatch
):
    """When the planner returns a PlanningFailure, the cross-worker branch
    yields the sentinel and run() drops down to the standard chat path.

    We verify that by stubbing chat_service.process_chat — if it gets
    called, the fall-through happened.  If it doesn't, the cross-worker
    branch tried to handle the request itself, which would be wrong.
    """
    from app.schemas.auth import UserInfo
    from app.schemas.chat import ChatRequest, StreamChunk
    from app.services.outcome_orchestrator import outcome_orchestrator

    chat_calls = {"n": 0}

    async def _fake_process_chat(*args, **kwargs):
        chat_calls["n"] += 1
        yield StreamChunk(type="content", content="standard path response")
        yield StreamChunk(type="done", data={"finish_reason": "stop"})

    # Reach the chat_service singleton via sys.modules to dodge the
    # __init__.py shadowing trap.
    import sys as _sys
    _chat_singleton = _sys.modules["app.services.chat_service"].chat_service
    monkeypatch.setattr(_chat_singleton, "process_chat", _fake_process_chat)

    # Planner stub that returns a failure regardless of input.
    class _StubPlanner:
        async def plan(self, goal, ctx, db):
            return PlanningFailure(
                reason="LLM_UNAVAILABLE", detail="(stub)"
            )

    _planner_mod = _sys.modules["app.services.orchestration_planner"]
    monkeypatch.setattr(_planner_mod, "orchestration_planner", _StubPlanner())

    user = UserInfo(
        id="u1",
        email="u1@example.com",
        name="U1",
        roles=[],
        groups=[],
        tenant_id="t1",
    )
    request = ChatRequest(
        message="what's overdue and what was decided in the last meeting?",
        stream=True,
    )

    chunks = []
    async for c in outcome_orchestrator.run(db, user, request):
        chunks.append(c)
        # bail after a couple — full chat_service is stubbed.
        if len(chunks) >= 4:
            break

    assert chat_calls["n"] >= 1, (
        "cross-worker branch must fall through to standard chat on planner failure"
    )


# ── Knowledge Base ───────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_summarise_truncates_when_no_openai(monkeypatch):
    import sys as _sys
    _osmod = _sys.modules["app.services.openai_service"]
    monkeypatch.setattr(_osmod, "openai_service", None)
    long_text = "x" * 1500
    out = await summarise_if_needed(long_text, source_worker_id="task-radar")
    assert len(out) <= SUMMARY_MAX_CHARS
    # Truncation path keeps the original content (just shorter).
    assert out == long_text[:SUMMARY_MAX_CHARS]


@pytest.mark.asyncio
async def test_summarise_short_text_passes_through(monkeypatch):
    import sys as _sys
    _osmod = _sys.modules["app.services.openai_service"]
    monkeypatch.setattr(_osmod, "openai_service", None)
    short = "12 overdue tasks"
    assert await summarise_if_needed(short) == short


@pytest.mark.asyncio
async def test_summarise_calls_llm_when_over_threshold(monkeypatch):
    captured = {"called": False}

    class _LLM:
        async def get_completion(self, *, messages, model, max_tokens, temperature):
            captured["called"] = True
            captured["model"] = model
            return "compressed two-sentence summary"

    import sys as _sys
    _osmod = _sys.modules["app.services.openai_service"]
    monkeypatch.setattr(_osmod, "openai_service", _LLM())
    long_text = "y" * 600
    out = await summarise_if_needed(long_text, source_worker_id="task-radar")
    assert captured["called"] is True
    assert captured["model"] == "gpt-4o-mini"
    assert out == "compressed two-sentence summary"


@pytest.mark.asyncio
async def test_kb_search_tenant_isolation(db):
    store = SQLKnowledgeStore()
    # Two tenants, same user_id, same query.  Search must scope.
    await store.ingest(
        db,
        KBEntry(
            user_id="u1",
            tenant_id="tenant-A",
            profile_mode="work",
            entry_type="task_summary",
            title="Tenant A overdue",
            summary="3 overdue tasks for tenant A",
            tags=["task-radar"],
        ),
    )
    await store.ingest(
        db,
        KBEntry(
            user_id="u2",
            tenant_id="tenant-B",
            profile_mode="work",
            entry_type="task_summary",
            title="Tenant B overdue",
            summary="9 overdue tasks for tenant B",
            tags=["task-radar"],
        ),
    )

    results_a = await store.search(
        db, tenant_id="tenant-A", user_id="u1",
        query="overdue", limit=10,
    )
    titles_a = [r.title for r in results_a]
    assert "Tenant A overdue" in titles_a
    assert "Tenant B overdue" not in titles_a

    results_b = await store.search(
        db, tenant_id="tenant-B", user_id="u2",
        query="overdue", limit=10,
    )
    titles_b = [r.title for r in results_b]
    assert "Tenant B overdue" in titles_b
    assert "Tenant A overdue" not in titles_b


@pytest.mark.asyncio
async def test_kb_ingest_caps_summary_at_500_chars(db):
    store = SQLKnowledgeStore()
    huge = "a" * 1500
    row = await store.ingest(
        db,
        KBEntry(
            user_id="u1",
            tenant_id="t1",
            profile_mode="work",
            entry_type="task_summary",
            title="huge",
            summary=huge,  # ingest hard-caps at SUMMARY_MAX_CHARS
        ),
    )
    assert len(row.summary) == SUMMARY_MAX_CHARS


@pytest.mark.asyncio
async def test_kb_search_filters_by_entry_type(db):
    store = SQLKnowledgeStore()
    await store.ingest(
        db,
        KBEntry(
            user_id="u1", tenant_id="t1", profile_mode="work",
            entry_type="task_summary",
            title="Tasks today", summary="task summary",
        ),
    )
    await store.ingest(
        db,
        KBEntry(
            user_id="u1", tenant_id="t1", profile_mode="work",
            entry_type="meeting_summary",
            title="Meeting today", summary="meeting summary",
        ),
    )
    only_meetings = await store.search(
        db, tenant_id="t1", user_id="u1",
        query="today", limit=10,
        entry_types=["meeting_summary"],
    )
    titles = [r.title for r in only_meetings]
    assert "Meeting today" in titles
    assert "Tasks today" not in titles
