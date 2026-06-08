"""
Deterministic integration tests for the Task Radar worker.

These prove the cross-agent contract WITHOUT needing Azure / Graph / network:
  1. The MCP dispatcher accepts a create_followup_tasks call and returns
     {status: accepted} immediately (async), validating X-Api-Key.
  2. run_create_followup_tasks produces a callback payload that VALIDATES
     against Mela's real IngestResultRequest schema (the /ingest/result
     contract) — both on success and on Graph-auth failure.

Run:  cd task-radar && python -m pytest test_worker.py -q
"""

from __future__ import annotations

import os
import sys
from unittest.mock import AsyncMock, patch

import pytest

_HERE = os.path.dirname(os.path.abspath(__file__))
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)
# Make Mela's backend importable so we can validate against the REAL
# IngestResultRequest contract the worker must satisfy.
_BACKEND = os.path.join(os.path.dirname(_HERE), "backend")
if os.path.isdir(_BACKEND) and _BACKEND not in sys.path:
    sys.path.insert(0, _BACKEND)

os.environ.setdefault("TASK_RADAR_MCP_API_KEY", "test-mcp-key")

import main  # noqa: E402
from capabilities import create_followup_tasks as cap  # noqa: E402


# ── 1. MCP dispatcher (ASGI transport — version-agnostic) ───────────────────

def _client():
    from httpx import ASGITransport, AsyncClient
    return AsyncClient(
        transport=ASGITransport(app=main.app), base_url="http://test"
    )


@pytest.mark.asyncio
async def test_dispatcher_accepts_async_capability():
    with patch.object(main, "run_create_followup_tasks", AsyncMock()):
        async with _client() as client:
            resp = await client.post(
                "/",
                headers={"X-Api-Key": "test-mcp-key",
                         "X-Mela-Trace-Id": "trace-123"},
                json={
                    "tool": "create_followup_tasks",
                    "arguments": {
                        "user_id": "u1", "tenant_id": "t1",
                        "mela_task_id": "task-abc",
                        "items": [{"title": "Review HIPAA logs"}],
                    },
                },
            )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["status"] == "accepted"
    assert body["task_id"] == "task-abc"
    assert body["trace_id"] == "trace-123"


@pytest.mark.asyncio
async def test_dispatcher_rejects_bad_key():
    async with _client() as client:
        resp = await client.post(
            "/",
            headers={"X-Api-Key": "wrong"},
            json={"tool": "create_followup_tasks", "arguments": {}},
        )
    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_health_deep_probe():
    with patch.object(cap, "get_graph_token", AsyncMock(return_value=None)):
        async with _client() as client:
            resp = await client.get("/health", params={"deep": "true"})
    assert resp.status_code == 200
    j = resp.json()
    assert j["worker"] == "task-radar"
    assert j["graph"] in ("ok", "unreachable")


# ── 2. Callback contract (success) ──────────────────────────────────────────

@pytest.mark.asyncio
async def test_create_followup_tasks_success_callback_matches_contract():
    from app.api.endpoints.orchestration_ingest import IngestResultRequest

    captured = {}

    async def _fake_send_callback(payload):
        captured.update(payload)

    async def _fake_create_one(token, *, plan_id, bucket_id, item):
        return {
            "ok": True,
            "title": item["title"],
            "task_id": f"planner-{item['title'][:4]}",
            "plan_id": plan_id,
            "web_url": "https://tasks.office.com/Home/Task/x",
            "due_date": item.get("due_date"),
        }

    with patch.object(cap, "get_graph_token", AsyncMock(return_value="tok")), \
         patch.object(cap, "_create_one_task", _fake_create_one), \
         patch.object(cap, "_send_callback", _fake_send_callback):
        await cap.run_create_followup_tasks(
            arguments={
                "user_id": "u1", "tenant_id": "t1",
                "plan_id": "plan-1",
                "items": [
                    {"title": "Review HIPAA access logs", "due_date": "2026-06-20"},
                    {"title": "Update incident response policy"},
                ],
            },
            trace_id="trace-xyz",
            mela_task_id="task-xyz",
        )

    # The captured payload MUST validate against Mela's real ingest schema.
    parsed = IngestResultRequest(**captured)
    assert parsed.task_id == "task-xyz"
    assert parsed.trace_id == "trace-xyz"
    assert parsed.capability == "create_followup_tasks"
    assert parsed.success is True
    assert parsed.data["created_count"] == 2
    assert len(parsed.data["created_tasks"]) == 2
    assert "Review HIPAA access logs" in parsed.summary


# ── 3. Callback contract (graph auth failure) ───────────────────────────────

@pytest.mark.asyncio
async def test_create_followup_tasks_graph_auth_failure_callback():
    from app.api.endpoints.orchestration_ingest import IngestResultRequest

    captured = {}

    async def _fake_send_callback(payload):
        captured.update(payload)

    with patch.object(cap, "get_graph_token", AsyncMock(return_value=None)), \
         patch.object(cap, "_send_callback", _fake_send_callback):
        await cap.run_create_followup_tasks(
            arguments={"user_id": "u1", "tenant_id": "t1",
                       "plan_id": "plan-1",
                       "items": [{"title": "X"}]},
            trace_id="t", mela_task_id="task-fail",
        )

    parsed = IngestResultRequest(**captured)
    assert parsed.success is False
    assert parsed.error_code == "GRAPH_AUTH_FAILED"
    assert parsed.error_retryable is True


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q"]))
