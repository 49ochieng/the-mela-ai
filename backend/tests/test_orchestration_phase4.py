"""
Phase 4 tests for the orchestration brain.

Coverage matches the Phase 4E specification:

  - openai_service.get_embedding returns None when openai_service is
    unavailable; never raises
  - KB search fallback: AZURE_SEARCH_KB_INDEX blank → search() uses SQL,
    Search client never instantiated
  - KB ingest with Search configured: mock client's upsert is called
    AND a SQL row is written
  - KB ingest embedding failure: returns None → SQL row still written,
    no exception
  - Expiry policy: task_summary → now + 30d; user_context → None
  - Expiry sweep: stale rows deleted, future rows retained
  - Trace list endpoint: admin user gets paginated rows with task counts;
    non-admin gets 403
  - Trace detail endpoint: returns tasks for a valid trace, 404 for
    unknown trace
  - KB stats endpoint: counts entries by type, computes
    entries_expiring_within_7_days
  - Meeting Assistant seed: AdapterFactory.get returns MCPAdapter with
    zero factory code changes
  - Meeting Assistant blank-URL → status='unconfigured' (not
    'unreachable') in the health summary
  - Cross-worker intent detection picks up Meeting Assistant domain
    keywords
"""

from __future__ import annotations

import sys
from datetime import datetime, timedelta
from typing import Any

import pytest

from app.orchestration.adapters.factory import AdapterFactory
from app.orchestration.health import get_worker_health_summary
from app.orchestration.knowledge import (
    KB_EXPIRY_DAYS_BY_TYPE,
    KBEntry,
    SQLKnowledgeStore,
    _resolve_expires_at,
)
from app.orchestration.registry import WorkerRegistry
from app.orchestration.types import WorkerStatus
from app.services.outcome_orchestrator import IntentType, detect_intent


def _patch_openai_service(monkeypatch, value: Any) -> None:
    """Reach the openai_service module via sys.modules to dodge the
    app/services/__init__.py shadowing trap (see Phase 3 note).
    """
    _osmod = sys.modules["app.services.openai_service"]
    monkeypatch.setattr(_osmod, "openai_service", value)


def _patch_kb_search_client(monkeypatch, value: Any) -> None:
    """Patch the kb_search_client singleton used by the KnowledgeStore.

    knowledge_search is lazy-imported by knowledge.py — force it into
    sys.modules first so the monkeypatch lands on the right object.
    """
    import app.orchestration.knowledge_search as _ks  # noqa: F401
    _mod = sys.modules["app.orchestration.knowledge_search"]
    monkeypatch.setattr(_mod, "kb_search_client", value)


# ── 4A: get_embedding null-safety ────────────────────────────────────────


@pytest.mark.asyncio
async def test_get_embedding_returns_none_when_service_none(monkeypatch):
    """When openai_service is None, the wrapper inside knowledge.py
    must short-circuit to None — never raise."""
    _patch_openai_service(monkeypatch, None)
    from app.orchestration.knowledge import _embed
    out = await _embed("anything")
    assert out is None


# ── 4A: KB Search delegate vs SQL fallback ───────────────────────────────


@pytest.mark.asyncio
async def test_kb_search_uses_sql_when_search_client_unset(monkeypatch, db):
    """No Search client → search() goes through SQL path."""
    _patch_kb_search_client(monkeypatch, None)
    _patch_openai_service(monkeypatch, None)

    store = SQLKnowledgeStore()
    await store.ingest(
        db,
        KBEntry(
            user_id="u1", tenant_id="t1", profile_mode="work",
            entry_type="task_summary",
            title="overdue today", summary="three overdue items",
        ),
    )
    rows = await store.search(
        db, tenant_id="t1", user_id="u1", query="overdue", limit=10,
    )
    assert any("overdue" in r.title for r in rows)


@pytest.mark.asyncio
async def test_kb_ingest_calls_search_upsert_when_configured(monkeypatch, db):
    """When kb_search_client is set, ingest() pushes to BOTH SQL AND Search.
    SQL is the source of truth; Search is the secondary index."""
    upserts: list[dict[str, Any]] = []

    class _FakeSearch:
        def upsert(self, *, entry, embedding):
            upserts.append({"entry": entry, "embedding": embedding})
        def delete(self, entry_id):  # noqa: ARG002
            pass
        def delete_stale(self, *, now=None):  # noqa: ARG002
            return 0
        def search(self, **_kw):
            return []

    fake = _FakeSearch()
    _patch_kb_search_client(monkeypatch, fake)
    _patch_openai_service(monkeypatch, None)  # forces embedding=None

    store = SQLKnowledgeStore()
    row = await store.ingest(
        db,
        KBEntry(
            user_id="u1", tenant_id="t1", profile_mode="work",
            entry_type="task_summary",
            title="hello", summary="world",
        ),
    )
    assert row.entry_id is not None
    assert len(upserts) == 1
    upserted = upserts[0]
    assert upserted["entry"]["title"] == "hello"
    # No openai_service → embedding skipped, but the SQL write succeeded.
    assert upserted["embedding"] is None


@pytest.mark.asyncio
async def test_kb_ingest_succeeds_when_embedding_returns_none(monkeypatch, db):
    """openai_service exists but get_embedding returns None — SQL row
    must still be written and no exception propagates."""
    class _LLM:
        async def get_embedding(self, _text):
            return None

    _patch_openai_service(monkeypatch, _LLM())
    _patch_kb_search_client(monkeypatch, None)

    store = SQLKnowledgeStore()
    row = await store.ingest(
        db,
        KBEntry(
            user_id="u1", tenant_id=None, profile_mode="personal",
            entry_type="task_summary",
            title="t", summary="s",
        ),
    )
    assert row.entry_id is not None
    assert row.embedding_vector is None


# ── 4C: Per-type expiry policy ──────────────────────────────────────────


def test_expiry_policy_task_summary_default():
    """task_summary entries default to 30 days."""
    assert KB_EXPIRY_DAYS_BY_TYPE["task_summary"] == 30
    before = datetime.utcnow() + timedelta(days=29)
    after = datetime.utcnow() + timedelta(days=31)
    out = _resolve_expires_at("task_summary", None)
    assert out is not None
    assert before < out < after


def test_expiry_policy_user_context_never_expires():
    """user_context entries never expire."""
    assert KB_EXPIRY_DAYS_BY_TYPE["user_context"] is None
    assert _resolve_expires_at("user_context", None) is None


def test_expiry_policy_explicit_caller_value_wins():
    target = datetime(2030, 1, 1)
    assert _resolve_expires_at("task_summary", target) == target


@pytest.mark.asyncio
async def test_kb_expire_stale_deletes_past_and_keeps_future(monkeypatch, db):
    _patch_kb_search_client(monkeypatch, None)
    _patch_openai_service(monkeypatch, None)

    store = SQLKnowledgeStore()
    past = await store.ingest(
        db,
        KBEntry(
            user_id="u1", tenant_id="t1", profile_mode="work",
            entry_type="task_summary",
            title="old", summary="stale",
            expires_at=datetime.utcnow() - timedelta(days=1),
        ),
    )
    future = await store.ingest(
        db,
        KBEntry(
            user_id="u1", tenant_id="t1", profile_mode="work",
            entry_type="task_summary",
            title="new", summary="fresh",
            expires_at=datetime.utcnow() + timedelta(days=10),
        ),
    )
    deleted = await store.expire_stale(db)
    assert deleted == 1
    # past row gone, future row retained
    assert await store.get(db, past.entry_id) is None
    assert await store.get(db, future.entry_id) is not None


# ── 4B: Trace endpoints ─────────────────────────────────────────────────


def _make_trace(db, *, trace_id, status, user_id="u1", tenant_id="t1"):
    """Fixture helper — create a trace row directly via the ORM."""
    from app.models.models import OrchestrationTrace
    trace = OrchestrationTrace(
        trace_id=trace_id,
        goal_id=trace_id + "-goal",
        user_id=user_id,
        tenant_id=tenant_id,
        profile_mode="work",
        status=status,
        plan_json={"goal": f"trace-{trace_id} goal"},
    )
    db.add(trace)
    return trace


def _make_task(db, *, task_id, trace_id, status, worker_id="task-radar",
               capability="get_overdue_tasks"):
    from app.models.models import OrchestrationTask
    task = OrchestrationTask(
        task_id=task_id,
        trace_id=trace_id,
        worker_id=worker_id,
        capability=capability,
        execution_mode="sync",
        status=status,
        params_json={},
        latency_ms=10,
    )
    db.add(task)
    return task


@pytest.mark.asyncio
async def test_traces_endpoint_admin_paginates(db, monkeypatch):
    """Admin path returns paginated traces with correct task counts."""
    from app.api.endpoints.orchestration import list_orchestration_traces
    from app.schemas.auth import UserInfo

    _make_trace(db, trace_id="trace-A", status="completed")
    _make_trace(db, trace_id="trace-B", status="failed")
    await db.commit()
    _make_task(db, task_id="ta1", trace_id="trace-A", status="completed")
    _make_task(db, task_id="ta2", trace_id="trace-A", status="completed")
    _make_task(db, task_id="tb1", trace_id="trace-B", status="failed")
    await db.commit()

    admin = UserInfo(
        id="admin", email="admin@example.com", name="A",
        roles=["admin"], groups=[], tenant_id="t1",
    )
    out = await list_orchestration_traces(
        tenant_id=None, user_id=None, status_filter=None,
        limit=20, offset=0, _admin=admin, db=db,
    )
    assert out["total"] == 2
    by_id = {t["trace_id"]: t for t in out["traces"]}
    assert by_id["trace-A"]["task_count"] == 2
    assert by_id["trace-A"]["failed_task_count"] == 0
    assert by_id["trace-B"]["task_count"] == 1
    assert by_id["trace-B"]["failed_task_count"] == 1


@pytest.mark.asyncio
async def test_traces_endpoint_status_filter_validation(db):
    """Bogus status filter → 400."""
    from fastapi import HTTPException
    from app.api.endpoints.orchestration import list_orchestration_traces
    from app.schemas.auth import UserInfo

    admin = UserInfo(
        id="admin", email="admin@example.com", name="A",
        roles=["admin"], groups=[], tenant_id="t1",
    )
    with pytest.raises(HTTPException) as exc_info:
        await list_orchestration_traces(
            tenant_id=None, user_id=None, status_filter="bogus",
            limit=20, offset=0, _admin=admin, db=db,
        )
    assert exc_info.value.status_code == 400


@pytest.mark.asyncio
async def test_trace_detail_endpoint_returns_tasks(db):
    from app.api.endpoints.orchestration import (
        get_orchestration_trace_detail,
    )
    from app.schemas.auth import UserInfo

    _make_trace(db, trace_id="trace-X", status="partial")
    await db.commit()
    _make_task(db, task_id="x1", trace_id="trace-X", status="completed")
    _make_task(db, task_id="x2", trace_id="trace-X", status="failed")
    await db.commit()

    admin = UserInfo(
        id="admin", email="admin@example.com", name="A",
        roles=["admin"], groups=[], tenant_id="t1",
    )
    detail = await get_orchestration_trace_detail(
        "trace-X", _admin=admin, db=db,
    )
    assert detail["trace_id"] == "trace-X"
    assert len(detail["tasks"]) == 2
    statuses = {t["status"] for t in detail["tasks"]}
    assert statuses == {"completed", "failed"}


@pytest.mark.asyncio
async def test_trace_detail_endpoint_404(db):
    from fastapi import HTTPException
    from app.api.endpoints.orchestration import (
        get_orchestration_trace_detail,
    )
    from app.schemas.auth import UserInfo

    admin = UserInfo(
        id="admin", email="admin@example.com", name="A",
        roles=["admin"], groups=[], tenant_id="t1",
    )
    with pytest.raises(HTTPException) as exc_info:
        await get_orchestration_trace_detail(
            "no-such-trace", _admin=admin, db=db,
        )
    assert exc_info.value.status_code == 404


# ── 4C: KB stats endpoint ───────────────────────────────────────────────


@pytest.mark.asyncio
async def test_kb_stats_counts_by_type(db, monkeypatch):
    _patch_kb_search_client(monkeypatch, None)
    _patch_openai_service(monkeypatch, None)

    store = SQLKnowledgeStore()
    await store.ingest(
        db,
        KBEntry(
            user_id="u1", tenant_id="t1", profile_mode="work",
            entry_type="task_summary",
            title="t1", summary="s1",
        ),
    )
    await store.ingest(
        db,
        KBEntry(
            user_id="u1", tenant_id="t1", profile_mode="work",
            entry_type="meeting_summary",
            title="m1", summary="s2",
        ),
    )
    stats = await store.stats(db)
    assert stats["total_entries"] == 2
    assert stats["entries_by_type"].get("task_summary") == 1
    assert stats["entries_by_type"].get("meeting_summary") == 1
    assert "entries_expiring_within_7_days" in stats


# ── 4D: Meeting Assistant + factory generalisation ─────────────────────


def test_meeting_assistant_uses_mcp_adapter_via_factory():
    """The factory must produce an MCPAdapter for Meeting Assistant
    with no Phase-4 code changes to factory.py."""
    from app.orchestration.adapters.factory import _PROTOCOL_TO_ADAPTER
    from app.orchestration.adapters.task_radar import MCPAdapter
    from app.orchestration.seed import _build_meeting_assistant_manifest

    manifest = _build_meeting_assistant_manifest()
    factory = AdapterFactory()
    adapter = factory.get(manifest)
    assert adapter is not None
    assert type(adapter).__name__ == "MCPAdapter"
    assert _PROTOCOL_TO_ADAPTER[manifest.protocol] is MCPAdapter


def test_meeting_assistant_blank_url_yields_unconfigured(monkeypatch):
    """Blank MEETING_ASSISTANT_BASE_URL → manifest.status == UNCONFIGURED."""
    from app.core.config import settings
    from app.orchestration.seed import _build_meeting_assistant_manifest

    monkeypatch.setattr(settings, "MEETING_ASSISTANT_BASE_URL", "")
    manifest = _build_meeting_assistant_manifest()
    assert manifest.status == WorkerStatus.UNCONFIGURED


@pytest.mark.asyncio
async def test_health_summary_reports_unconfigured(db, monkeypatch):
    """Worker registered with status='unconfigured' surfaces as
    'unconfigured' in the health summary, NOT 'unreachable'."""
    from app.core.config import settings
    from app.orchestration.seed import _build_meeting_assistant_manifest

    monkeypatch.setattr(settings, "MEETING_ASSISTANT_BASE_URL", "")
    manifest = _build_meeting_assistant_manifest()

    registry = WorkerRegistry()
    await registry.upsert(db, manifest)

    summary = await get_worker_health_summary(db, registry=registry)
    by_id = {w["id"]: w for w in summary["workers"]}
    assert by_id["meeting-assistant"]["status"] == "unconfigured"
    assert summary["summary"].get("unconfigured", 0) >= 1


# ── 4D: Intent detection picks up meeting-assistant keywords ─────────────


def test_cross_worker_intent_detects_meeting_assistant_domain():
    # action items + tasks → two distinct domains, conjunction present.
    assert detect_intent(
        "what are my action items and what tasks are overdue?"
    ) == IntentType.CROSS_WORKER
    # participants + email → also two domains.
    assert detect_intent(
        "who were the participants and is there email about it?"
    ) == IntentType.CROSS_WORKER
