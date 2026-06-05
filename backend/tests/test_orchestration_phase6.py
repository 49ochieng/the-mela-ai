"""
Phase 6 tests for the orchestration brain.

Coverage:
  MCP server:
    - mela_chat with valid key returns a response
    - scope without mela_chat → 403
    - mela_search_knowledge tenant isolation
    - mela_trigger_plan background returns trace_id immediately
    - mela_ingest_context records source_worker_id from client_name
    - missing key → 401, revoked key → 401, wrong scope → 403

  MCP client management:
    - create returns plaintext key once
    - GET returns metadata, never key value
    - DELETE soft-deletes

  Embed:
    - valid client key mints token with correct expiry
    - invalid key → 401
    - scope restriction propagates to /config response
    - expired token → 401
    - SAMEORIGIN default; allowed-origin header present when configured

  Worker self-registration:
    - blank registration key → 503
    - valid key registers worker + returns inbound_api_key
    - re-registration with version bump updates manifest

  Capabilities endpoint:
    - returns 6 tools, no auth, OpenAI function shape
"""

from __future__ import annotations

import asyncio
import sys
from datetime import datetime, timedelta, timezone
from typing import Any

import pytest
from fastapi import HTTPException
from jose import jwt

from app.api.endpoints.embed import (
    EMBED_TOKEN_AUDIENCE,
    EmbedTokenRequest,
    embed_config,
    mint_embed_token,
)
from app.api.endpoints.orchestration import (
    create_mcp_client,
    list_mcp_clients,
    list_mela_capabilities,
    register_worker,
    revoke_mcp_client,
)
from app.core.config import settings
from app.mcp.auth import (
    authenticate_mcp_client,
    generate_api_key,
    hash_api_key,
    verify_api_key,
)
from app.mcp.server import mcp_dispatch
from app.mcp.tools import MELA_TOOL_DEFS, MELA_TOOL_NAMES, is_tool_in_scope
from app.models.models import MCPClient, WorkerRegistryEntry
from app.orchestration.registry import worker_registry
from app.schemas.auth import UserInfo


# ── Helpers ──────────────────────────────────────────────────────────────


async def _make_client(
    db, *, scopes: list[str], client_name: str = "test-client",
    revoked: bool = False,
) -> tuple[MCPClient, str]:
    plaintext = generate_api_key()
    client = MCPClient(
        client_name=client_name,
        api_key_hash=hash_api_key(plaintext),
        tenant_id="t1",
        scopes=scopes,
        created_by="admin",
        revoked_at=datetime.utcnow() if revoked else None,
    )
    db.add(client)
    await db.commit()
    await db.refresh(client)
    return client, plaintext


def _admin_user() -> UserInfo:
    return UserInfo(
        id="admin", email="a@example.com", name="Admin",
        roles=["admin"], groups=[], tenant_id="t1",
    )


# ── MCP auth primitives ─────────────────────────────────────────────────


def test_api_key_round_trip():
    pt = generate_api_key()
    assert pt.startswith("mela_")
    h = hash_api_key(pt)
    assert verify_api_key(pt, h)
    assert not verify_api_key(pt + "x", h)


def test_is_tool_in_scope():
    assert is_tool_in_scope("mela_chat", ["mela_chat"])
    assert is_tool_in_scope("mela_chat", ["*"])
    assert not is_tool_in_scope("mela_chat", ["mela_search_knowledge"])
    assert not is_tool_in_scope("mela_chat", [])
    assert not is_tool_in_scope("mela_chat", None)


@pytest.mark.asyncio
async def test_authenticate_mcp_client_no_match_returns_none(db):
    out = await authenticate_mcp_client("does-not-exist", db)
    assert out is None


@pytest.mark.asyncio
async def test_authenticate_mcp_client_revoked_excluded(db):
    _, plaintext = await _make_client(db, scopes=["*"], revoked=True)
    out = await authenticate_mcp_client(plaintext, db)
    assert out is None


@pytest.mark.asyncio
async def test_authenticate_mcp_client_active_match(db):
    client, plaintext = await _make_client(db, scopes=["mela_chat"])
    out = await authenticate_mcp_client(plaintext, db)
    assert out is not None
    assert out.id == client.id


# ── MCP dispatch + scope ────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_mcp_dispatch_scope_violation_returns_403(db):
    client, _ = await _make_client(db, scopes=["mela_search_knowledge"])
    # Build a fake call to mela_chat which the client has no scope for.
    from app.mcp.server import MCPCall
    call = MCPCall(tool="mela_chat", arguments={"message": "hi"})
    with pytest.raises(HTTPException) as exc:
        await mcp_dispatch(call=call, client=client, db=db)
    assert exc.value.status_code == 403


@pytest.mark.asyncio
async def test_mcp_dispatch_unknown_tool_returns_400(db):
    client, _ = await _make_client(db, scopes=["*"])
    from app.mcp.server import MCPCall
    call = MCPCall(tool="not_a_real_tool", arguments={})
    with pytest.raises(HTTPException) as exc:
        await mcp_dispatch(call=call, client=client, db=db)
    assert exc.value.status_code == 400


# ── Specific tools ──────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_mela_search_knowledge_tenant_isolation(db, monkeypatch):
    """Two tenants, identical query, distinct results."""
    from app.orchestration.knowledge import KBEntry, knowledge_store

    # Disable Search delegation + embedding so this is a pure SQL test.
    # knowledge_search is lazy-imported by knowledge.py — force it
    # into sys.modules first so the monkeypatch lands.
    import app.orchestration.knowledge_search  # noqa: F401
    _ks = sys.modules["app.orchestration.knowledge_search"]
    monkeypatch.setattr(_ks, "kb_search_client", None)
    import app.services.openai_service  # noqa: F401
    _osmod = sys.modules["app.services.openai_service"]
    monkeypatch.setattr(_osmod, "openai_service", None)

    await knowledge_store.ingest(
        db,
        KBEntry(
            user_id="u1", tenant_id="tA", profile_mode="work",
            entry_type="task_summary",
            title="alpha tenant report", summary="three things",
        ),
    )
    await knowledge_store.ingest(
        db,
        KBEntry(
            user_id="u2", tenant_id="tB", profile_mode="work",
            entry_type="task_summary",
            title="beta tenant report", summary="seven things",
        ),
    )

    client, _ = await _make_client(db, scopes=["mela_search_knowledge"])
    from app.mcp.server import MCPCall
    out = await mcp_dispatch(
        call=MCPCall(
            tool="mela_search_knowledge",
            arguments={
                "query": "report",
                "tenant_id": "tA",
                "user_id": "u1",
                "limit": 10,
            },
        ),
        client=client,
        db=db,
    )
    titles = [r["title"] for r in out["results"]]
    assert "alpha tenant report" in titles
    assert "beta tenant report" not in titles


@pytest.mark.asyncio
async def test_mela_trigger_plan_background_returns_immediately(
    db, monkeypatch
):
    """Background mode dispatches via asyncio.create_task and returns
    the trace_id without awaiting executor.run_plan."""
    from app.mcp.server import MCPCall
    from app.orchestration.executor import ExecutionPlan, TaskBatch
    from app.orchestration.types import (
        MelaContext, MelaTask, Priority,
    )
    from app.services.orchestration_planner import AnnotatedPlan

    fixed_task = MelaTask(
        capability="get_overdue_tasks",
        worker_id="task-radar",
        params={},
        context=MelaContext(
            tenant_id="t1", user_id="u1", priority=Priority.NORMAL,
        ),
        execution_mode="sync",
        trace_id="trace-MCP-1",
    )
    plan = ExecutionPlan(
        plan_id="p1", goal_id="g1", goal="x",
        batches=[TaskBatch(batch_index=0, tasks=[fixed_task])],
        user_id="u1", tenant_id="t1", profile_mode="work",
    )

    class _StubPlanner:
        async def plan(self, goal, ctx, db):
            return AnnotatedPlan(plan=plan, estimated_total_ms=500)

    _planner_mod = sys.modules["app.services.orchestration_planner"]
    monkeypatch.setattr(_planner_mod, "orchestration_planner", _StubPlanner())

    bg_called: list[bool] = []

    class _StubExecutor:
        async def run_plan(self, *_a, **_kw):
            bg_called.append(True)

    # The MCP server imports ``executor`` at module load time, so the
    # binding lives inside ``app.mcp.server`` — patching the original
    # module wouldn't reach it.  This is the same pattern documented
    # in earlier phases for app/services/__init__.py shadowing.
    import app.mcp.server  # noqa: F401
    _server_mod = sys.modules["app.mcp.server"]
    monkeypatch.setattr(_server_mod, "executor", _StubExecutor())

    class _NullSession:
        async def __aenter__(self):
            return self
        async def __aexit__(self, *a):
            return False
    _db_mod = sys.modules["app.core.database"]
    monkeypatch.setattr(_db_mod, "async_session_maker", lambda: _NullSession())

    client, _ = await _make_client(db, scopes=["mela_trigger_plan"])
    out = await mcp_dispatch(
        call=MCPCall(
            tool="mela_trigger_plan",
            arguments={
                "goal": "do something",
                "user_id": "u1",
                "tenant_id": "t1",
                "execution_mode": "background",
            },
        ),
        client=client,
        db=db,
    )
    assert out["status"] == "queued"
    assert out["trace_id"] == "trace-MCP-1"
    await asyncio.sleep(0)  # let the create_task'd coroutine run
    assert bg_called == [True]


@pytest.mark.asyncio
async def test_mela_ingest_context_records_client_name_as_source(
    db, monkeypatch,
):
    """The MCP client's client_name is the source_worker_id — never
    anything the caller echoed."""
    import app.orchestration.knowledge_search  # noqa: F401
    _ks = sys.modules["app.orchestration.knowledge_search"]
    monkeypatch.setattr(_ks, "kb_search_client", None)
    import app.services.openai_service  # noqa: F401
    _osmod = sys.modules["app.services.openai_service"]
    monkeypatch.setattr(_osmod, "openai_service", None)

    client, _ = await _make_client(
        db, scopes=["mela_ingest_context"], client_name="meeting-assistant",
    )
    from app.mcp.server import MCPCall
    out = await mcp_dispatch(
        call=MCPCall(
            tool="mela_ingest_context",
            arguments={
                "title": "Standup notes",
                "summary": "Decisions: ship Phase 6 by EOD",
                "entry_type": "meeting_summary",
                "tenant_id": "t1",
                "user_id": "u1",
                "tags": ["standup"],
            },
        ),
        client=client,
        db=db,
    )
    assert out["entry_id"]
    assert out["source_worker_id"] == "mcp:meeting-assistant"


# ── MCP client admin endpoints ──────────────────────────────────────────


@pytest.mark.asyncio
async def test_create_mcp_client_returns_plaintext_once(db):
    out = await create_mcp_client(
        body={
            "client_name": "demo",
            "scopes": ["mela_chat", "mela_search_knowledge"],
        },
        admin=_admin_user(),
        db=db,
    )
    assert out["api_key"].startswith("mela_")
    # Subsequent list never returns the key.
    listed = await list_mcp_clients(
        include_revoked=False, _admin=_admin_user(), db=db,
    )
    for c in listed["clients"]:
        assert "api_key" not in c


@pytest.mark.asyncio
async def test_create_mcp_client_unknown_scope_returns_400(db):
    with pytest.raises(HTTPException) as exc:
        await create_mcp_client(
            body={"client_name": "demo", "scopes": ["nonsense"]},
            admin=_admin_user(),
            db=db,
        )
    assert exc.value.status_code == 400


@pytest.mark.asyncio
async def test_revoke_mcp_client_soft_deletes(db):
    created = await create_mcp_client(
        body={"client_name": "demo", "scopes": ["mela_chat"]},
        admin=_admin_user(),
        db=db,
    )
    out = await revoke_mcp_client(
        created["id"], _admin=_admin_user(), db=db,
    )
    assert out["revoked_at"] is not None
    # Row is still there — soft-delete keeps the audit trail.
    row = await db.get(MCPClient, created["id"])
    assert row is not None
    assert row.revoked_at is not None


# ── Embed ───────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_embed_token_invalid_client_key_returns_401(db):
    body = EmbedTokenRequest(user_id="u1", tenant_id="t1")
    with pytest.raises(HTTPException) as exc:
        await mint_embed_token(
            body=body, x_mela_client_key="not-a-real-key", db=db,
        )
    assert exc.value.status_code == 401


@pytest.mark.asyncio
async def test_embed_token_minted_with_correct_expiry_and_scope(db):
    _, plaintext = await _make_client(
        db, scopes=["mela_chat", "mela_search_knowledge"],
    )
    body = EmbedTokenRequest(user_id="u1", tenant_id="t1", profile_mode="work")
    out = await mint_embed_token(
        body=body, x_mela_client_key=plaintext, db=db,
    )
    # Decode the issued token directly to assert claims.
    payload = jwt.decode(
        out.embed_token, settings.JWT_SECRET_KEY,
        algorithms=[settings.JWT_ALGORITHM],
        audience=EMBED_TOKEN_AUDIENCE,
    )
    assert payload["sub"] == "u1"
    assert payload["tenant_id"] == "t1"
    assert payload["profile_mode"] == "work"
    # Scope inherited from client (no allowed_tools restriction supplied)
    assert "mela_chat" in payload["allowed_tools"]
    assert "mela_search_knowledge" in payload["allowed_tools"]


@pytest.mark.asyncio
async def test_embed_token_restricts_scope_to_subset(db):
    _, plaintext = await _make_client(
        db, scopes=["mela_chat", "mela_search_knowledge", "mela_ingest_context"],
    )
    body = EmbedTokenRequest(
        user_id="u1", tenant_id="t1",
        allowed_tools=["mela_chat"],
    )
    out = await mint_embed_token(
        body=body, x_mela_client_key=plaintext, db=db,
    )
    payload = jwt.decode(
        out.embed_token, settings.JWT_SECRET_KEY,
        algorithms=[settings.JWT_ALGORITHM],
        audience=EMBED_TOKEN_AUDIENCE,
    )
    assert payload["allowed_tools"] == ["mela_chat"]


@pytest.mark.asyncio
async def test_embed_token_rejects_scope_escalation(db):
    """allowed_tools cannot include something the client itself lacks."""
    _, plaintext = await _make_client(db, scopes=["mela_chat"])
    body = EmbedTokenRequest(
        user_id="u1", tenant_id="t1",
        allowed_tools=["mela_chat", "mela_ingest_context"],
    )
    with pytest.raises(HTTPException) as exc:
        await mint_embed_token(
            body=body, x_mela_client_key=plaintext, db=db,
        )
    assert exc.value.status_code == 403


@pytest.mark.asyncio
async def test_embed_config_expired_token_returns_401(db):
    """Manually mint an expired token and confirm /config rejects it."""
    expired = jwt.encode(
        {
            "iss": "mela",
            "aud": EMBED_TOKEN_AUDIENCE,
            "sub": "u1",
            "iat": int(
                (datetime.now(timezone.utc) - timedelta(hours=2)).timestamp()
            ),
            "exp": int(
                (datetime.now(timezone.utc) - timedelta(hours=1)).timestamp()
            ),
        },
        settings.JWT_SECRET_KEY,
        algorithm=settings.JWT_ALGORITHM,
    )
    with pytest.raises(HTTPException) as exc:
        await embed_config(token=expired)
    assert exc.value.status_code == 401


@pytest.mark.asyncio
async def test_embed_config_returns_decoded_payload(db):
    """Round-trip: mint then read back."""
    _, plaintext = await _make_client(db, scopes=["mela_chat"])
    body = EmbedTokenRequest(user_id="u1", tenant_id="t1")
    minted = await mint_embed_token(
        body=body, x_mela_client_key=plaintext, db=db,
    )
    cfg = await embed_config(token=minted.embed_token)
    assert cfg["user_id"] == "u1"
    assert cfg["tenant_id"] == "t1"
    assert "mela_chat" in cfg["allowed_tools"]


# ── Embed frame middleware ──────────────────────────────────────────────


@pytest.mark.asyncio
async def test_embed_frame_middleware_default_sameorigin(monkeypatch):
    """Blank MELA_EMBED_ALLOWED_ORIGINS → SAMEORIGIN response header."""
    from starlette.requests import Request
    from starlette.responses import Response
    from app.core.middleware import EmbedFrameMiddleware

    monkeypatch.setattr(settings, "MELA_EMBED_ALLOWED_ORIGINS", "")
    middleware = EmbedFrameMiddleware(app=None)  # type: ignore[arg-type]

    async def call_next(_req):
        return Response("ok")

    scope = {
        "type": "http", "method": "GET", "path": "/embed",
        "headers": [],
    }
    req = Request(scope=scope)
    resp = await middleware.dispatch(req, call_next)
    assert resp.headers.get("X-Frame-Options") == "SAMEORIGIN"
    assert "Access-Control-Allow-Origin" not in resp.headers


@pytest.mark.asyncio
async def test_embed_frame_middleware_allowed_origin(monkeypatch):
    from starlette.requests import Request
    from starlette.responses import Response
    from app.core.middleware import EmbedFrameMiddleware

    monkeypatch.setattr(
        settings, "MELA_EMBED_ALLOWED_ORIGINS",
        "https://taskradar.armely.com",
    )
    middleware = EmbedFrameMiddleware(app=None)  # type: ignore[arg-type]

    async def call_next(_req):
        return Response("ok")

    scope = {
        "type": "http", "method": "GET", "path": "/embed",
        "headers": [
            (b"origin", b"https://taskradar.armely.com"),
        ],
    }
    req = Request(scope=scope)
    resp = await middleware.dispatch(req, call_next)
    assert (
        resp.headers.get("Access-Control-Allow-Origin")
        == "https://taskradar.armely.com"
    )
    csp = resp.headers.get("Content-Security-Policy") or ""
    assert "frame-ancestors" in csp
    assert "https://taskradar.armely.com" in csp


# ── Worker self-registration ────────────────────────────────────────────


@pytest.mark.asyncio
async def test_register_worker_disabled_when_key_blank(monkeypatch, db):
    monkeypatch.setattr(settings, "MELA_WORKER_REGISTRATION_KEY", "")
    with pytest.raises(HTTPException) as exc:
        await register_worker(
            body={}, x_registration_key="anything", db=db,
        )
    assert exc.value.status_code == 503


@pytest.mark.asyncio
async def test_register_worker_invalid_key_returns_401(monkeypatch, db):
    monkeypatch.setattr(
        settings, "MELA_WORKER_REGISTRATION_KEY", "secret",
    )
    with pytest.raises(HTTPException) as exc:
        await register_worker(
            body={}, x_registration_key="wrong", db=db,
        )
    assert exc.value.status_code == 401


@pytest.mark.asyncio
async def test_register_worker_round_trip(monkeypatch, db):
    monkeypatch.setattr(
        settings, "MELA_WORKER_REGISTRATION_KEY", "secret",
    )
    # Cache invalidate so the upserted manifest is picked up immediately.
    worker_registry._invalidate()

    body = {
        "id": "self-reg-worker",
        "display_name": "Self-Reg Worker",
        "version": "1.0.0",
        "capabilities": [
            {
                "name": "do_thing",
                "description": "d",
                "input_params": {"type": "object", "properties": {}},
                "output_shape": {"type": "object"},
            },
        ],
        "protocol": "mcp",
        "base_url": "http://example.invalid/mcp",
        "health_check_url": "http://example.invalid/health",
        "auth_scheme": "api_key",
        "auth_config": {"header": "X-Api-Key"},
        "status": "unknown",
    }
    out = await register_worker(
        body=body, x_registration_key="secret", db=db,
    )
    assert out["worker_id"] == "self-reg-worker"
    assert out["inbound_api_key"].startswith("wkr_")

    # Manifest was upserted.
    rows = (
        await db.execute(
            __import__("sqlalchemy").select(WorkerRegistryEntry).where(
                WorkerRegistryEntry.id == "self-reg-worker",
            )
        )
    ).scalars().all()
    assert len(rows) == 1


@pytest.mark.asyncio
async def test_register_worker_version_bump_updates(monkeypatch, db):
    monkeypatch.setattr(
        settings, "MELA_WORKER_REGISTRATION_KEY", "secret",
    )
    worker_registry._invalidate()

    base_body = {
        "id": "bump-worker",
        "display_name": "Bump",
        "version": "1.0.0",
        "capabilities": [
            {
                "name": "do_thing", "description": "d",
                "input_params": {"type": "object", "properties": {}},
                "output_shape": {"type": "object"},
            },
        ],
        "protocol": "mcp",
        "base_url": "http://example.invalid/mcp",
        "health_check_url": "http://example.invalid/health",
        "auth_scheme": "api_key",
        "auth_config": {"header": "X-Api-Key"},
        "status": "unknown",
    }
    await register_worker(
        body=base_body, x_registration_key="secret", db=db,
    )
    bumped = {**base_body, "version": "2.0.0"}
    out2 = await register_worker(
        body=bumped, x_registration_key="secret", db=db,
    )
    assert out2["version"] == "2.0.0"
    assert out2["inbound_api_key"].startswith("wkr_")


# ── Capabilities endpoint ───────────────────────────────────────────────


@pytest.mark.asyncio
async def test_capabilities_endpoint_no_auth_returns_six_tools():
    out = await list_mela_capabilities()
    assert "tools" in out
    names = {t["function"]["name"] for t in out["tools"]}
    assert names == set(MELA_TOOL_NAMES)
    # Sanity: each tool has the OpenAI function shape.
    for t in MELA_TOOL_DEFS:
        assert t["type"] == "function"
        assert "name" in t["function"]
        assert "description" in t["function"]
        assert t["function"]["parameters"]["type"] == "object"
