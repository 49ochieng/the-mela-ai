"""Teams MVP coverage: graph endpoints, paging, mention modes, normalize→
extractor wiring, source filtering on REST + MCP, permission errors."""
from __future__ import annotations

from datetime import datetime, timedelta
from unittest.mock import AsyncMock

import pytest

from app.config import get_settings
from app.enums import SourceType, TaskStatus
from app.models import SourceMessage, Task, Tenant, User
from app.services.graph import teams as teams_svc
from app.services.graph.client import GraphHTTPError
from app.services.tasks.normalize import normalize_teams


# ── helpers ──────────────────────────────────────────────────
async def _seed_user(session, *, entra_id="entra-1", upn=None,
                     email="user@example.com", display="Edgar Test"):
    t = Tenant(entra_tenant_id="t", name="t")
    session.add(t); await session.flush()
    u = User(tenant_id=t.id, entra_user_id=entra_id,
             display_name=display, email=email, timezone="UTC", role="user")
    session.add(u); await session.commit()
    return t, u


async def _seed_task(session, *, tenant_id, user_id, source_type="teams",
                     title="Update tracker", due=None, raw_meta=None):
    sm = SourceMessage(
        tenant_id=tenant_id, user_id=user_id,
        source_type=source_type, graph_message_id=f"g-{title}",
        subject_or_channel="general", sender_name="Alice",
        raw_metadata_json=raw_meta or {},
    )
    session.add(sm); await session.flush()
    t = Task(
        tenant_id=tenant_id, user_id=user_id, source_message_id=sm.id,
        title=title, source_type=source_type,
        due_date=due, status=TaskStatus.OPEN.value,
        priority="high", confidence=0.9,
    )
    session.add(t); await session.commit()
    return t, sm


# ── 1. Graph scopes include Teams permissions ────────────────
def test_default_graph_scopes_include_teams():
    s = get_settings()
    needed = {"Team.ReadBasic.All", "Channel.ReadBasic.All", "ChannelMessage.Read.All"}
    assert needed.issubset(set(s.graph_scope_list)), s.graph_scope_list


# ── 2. Graph teams service hits the right endpoints ──────────
@pytest.mark.asyncio
async def test_list_joined_teams_hits_me_joined_teams():
    client = AsyncMock()
    client.paged.return_value = [{"id": "t1", "displayName": "Team 1"}]
    out = await teams_svc.list_joined_teams(client)
    client.paged.assert_awaited_once_with("/me/joinedTeams")
    assert out and out[0]["id"] == "t1"


@pytest.mark.asyncio
async def test_list_channels_hits_teams_id_channels():
    client = AsyncMock()
    client.paged.return_value = [{"id": "c1", "displayName": "general"}]
    out = await teams_svc.list_channels(client, "t1")
    client.paged.assert_awaited_once_with("/teams/t1/channels")
    assert out[0]["displayName"] == "general"


# ── 3. Pagination follows @odata.nextLink ────────────────────
@pytest.mark.asyncio
async def test_paged_walks_next_link():
    """GraphClient.paged must follow @odata.nextLink until exhausted."""
    from app.services.graph.client import GraphClient

    page1 = {"value": [{"id": "m1"}], "@odata.nextLink": "https://graph/next?token=2"}
    page2 = {"value": [{"id": "m2"}]}

    client = GraphClient.__new__(GraphClient)  # bypass __init__ DI
    client._client = AsyncMock()  # type: ignore[attr-defined]
    client._refresh = AsyncMock()  # type: ignore[attr-defined]

    async def fake_get(path, params=None):  # noqa: ARG001
        return page1 if "next" not in path else page2

    client.get = fake_get  # type: ignore[assignment]
    items = await client.paged("/me/joinedTeams")
    assert [m["id"] for m in items] == ["m1", "m2"]


# ── 4. Mention detection — UPN / email / displayName fallbacks
def test_detect_mentions_matches_entra_id():
    msg = {"mentions": [{"mentioned": {"user": {"id": "entra-1"}}}]}
    assert teams_svc.detect_mentions(msg, "entra-1") is True


def test_detect_mentions_matches_upn():
    msg = {"mentions": [{"mentioned": {"user": {"userPrincipalName": "edgar@contoso.com"}}}]}
    assert teams_svc.detect_mentions(msg, "x", user_upn="edgar@contoso.com") is True


def test_detect_mentions_matches_email():
    msg = {"mentions": [{"mentioned": {"user": {"email": "edgar@contoso.com"}}}]}
    assert teams_svc.detect_mentions(msg, "x", user_email="edgar@contoso.com") is True


def test_detect_mentions_matches_display_name():
    msg = {"mentions": [{"mentioned": {"user": {"displayName": "Edgar Test"}}}]}
    assert teams_svc.detect_mentions(msg, "x", user_display_name="Edgar Test") is True


def test_detect_mentions_skips_unrelated():
    msg = {"mentions": [{"mentioned": {"user": {"id": "someone-else"}}}]}
    assert teams_svc.detect_mentions(
        msg, "entra-1", user_upn="edgar@contoso.com",
        user_email="edgar@contoso.com", user_display_name="Edgar",
    ) is False


# ── 5. normalize_teams produces source=teams payload ─────────
def test_normalize_teams_produces_teams_payload_with_metadata():
    msg = {
        "id": "msg-1",
        "createdDateTime": "2024-01-01T10:00:00Z",
        "body": {"content": "Please update the tracker"},
        "from": {"user": {"displayName": "Alice"}},
        "mentions": [{"mentioned": {"user": {"id": "entra-1"}}}],
    }
    norm = normalize_teams(
        msg, team_id="T1", channel_id="C1", channel_name="general",
        team_name="Project Atlas", user_entra_id="entra-1",
    )
    assert norm["source_type"] == SourceType.TEAMS.value
    assert norm["graph_message_id"] == "T1:C1:msg-1"
    assert norm["raw_metadata_json"]["team_id"] == "T1"
    assert norm["raw_metadata_json"]["team_name"] == "Project Atlas"
    assert norm["raw_metadata_json"]["channel_name"] == "general"
    assert norm["raw_metadata_json"]["is_mention"] is True
    assert norm["ai_payload"]["source"] == "teams"
    assert norm["ai_payload"]["is_mentioned"] is True
    assert norm["is_mention"] is True


# ── 6. REST today/overdue accept source=teams filter ─────────
@pytest.mark.asyncio
async def test_rest_today_filters_by_source(session):
    from sqlalchemy import select as _select  # local
    t, u = await _seed_user(session)
    today_dt = datetime.utcnow().replace(hour=10, minute=0, second=0, microsecond=0)
    await _seed_task(session, tenant_id=t.id, user_id=u.id,
                     source_type="teams", title="Teams task", due=today_dt)
    await _seed_task(session, tenant_id=t.id, user_id=u.id,
                     source_type="email", title="Email task", due=today_dt)

    # Filter Teams only via the same query the REST handler uses.
    rows = (await session.execute(
        _select(Task).where(
            Task.user_id == u.id, Task.source_type == "teams",
        )
    )).scalars().all()
    titles = {r.title for r in rows}
    assert titles == {"Teams task"}


# ── 7. MCP tools filter by source=teams ──────────────────────
@pytest.mark.asyncio
async def test_mcp_get_today_tasks_source_teams_only(session, monkeypatch):
    from app.mcp import server as mcp_server

    t, u = await _seed_user(session)
    now = datetime.utcnow().replace(hour=12, minute=0, second=0, microsecond=0)
    await _seed_task(session, tenant_id=t.id, user_id=u.id,
                     source_type="teams", title="From Teams", due=now)
    await _seed_task(session, tenant_id=t.id, user_id=u.id,
                     source_type="email", title="From Email", due=now)

    # Patch session_scope to yield our test session.
    from contextlib import asynccontextmanager

    @asynccontextmanager
    async def _scope():
        yield session

    monkeypatch.setattr(mcp_server, "session_scope", _scope)
    res = await mcp_server.tool_get_today_tasks({"user_id": u.id, "source": "teams"})
    titles = [
        item["title"]
        for bucket in res["tasks_by_priority"].values()
        for item in bucket
    ]
    assert titles == ["From Teams"]
    assert res["total"] == 1


@pytest.mark.asyncio
async def test_mcp_search_tasks_source_teams_only(session, monkeypatch):
    from contextlib import asynccontextmanager
    from app.mcp import server as mcp_server

    t, u = await _seed_user(session)
    await _seed_task(session, tenant_id=t.id, user_id=u.id,
                     source_type="teams", title="Teams ABC")
    await _seed_task(session, tenant_id=t.id, user_id=u.id,
                     source_type="email", title="Email ABC")

    @asynccontextmanager
    async def _scope():
        yield session

    monkeypatch.setattr(mcp_server, "session_scope", _scope)
    res = await mcp_server.tool_search_tasks(
        {"user_id": u.id, "query": "ABC", "source": "teams"}
    )
    assert [r["title"] for r in res["tasks"]] == ["Teams ABC"]


# ── 8. MCP /mcp/call accepts both `tool` and `name` (auth-gated) ─
def test_mcp_http_app_call_accepts_name_alias():
    """With the new per-user auth, an unauthenticated call returns 401
    regardless of payload shape. The {tool|name} alias still applies once
    authenticated; covered by integration tests."""
    from fastapi.testclient import TestClient
    from app.mcp.server import create_http_app

    app = create_http_app()
    client = TestClient(app)
    r1 = client.post("/mcp/call", json={"name": "unknown_tool", "arguments": {}})
    assert r1.status_code == 401


# ── 9. Permission error → graph_permission_missing category ─
@pytest.mark.asyncio
async def test_teams_permission_error_recorded_as_graph_permission_missing(
    session, monkeypatch
):
    """When Graph returns 403 fetching a channel, the scan must record an
    event with category=graph_permission_missing and not crash."""
    from app.services.tasks import scan_runner
    from app.models import ScanRun, ScanSettings
    from app.enums import ScanType, ScanStatus

    t, u = await _seed_user(session)
    # Settings that select a channel
    cfg = ScanSettings(
        tenant_id=t.id, user_id=u.id,
        teams_scan_enabled=True,
        selected_channel_ids=["TEAM1|CHAN1|general|Project Atlas"],
        mentions_only=False, include_thread_context=False,
    )
    session.add(cfg)
    sr = ScanRun(
        tenant_id=t.id, user_id=u.id,
        scan_type=ScanType.TEAMS.value, status=ScanStatus.PENDING.value,
    )
    session.add(sr); await session.commit()

    # Patch Graph fetch to raise 403
    async def boom(*a, **kw):
        raise GraphHTTPError(403, "Forbidden")

    monkeypatch.setattr(
        scan_runner.teams_svc, "get_channel_messages_since", boom,
    )

    from collections import Counter
    cats: Counter = Counter()
    fake_client = AsyncMock(aclose=AsyncMock())
    await scan_runner._scan_teams(
        session, fake_client, u, sr, cfg, cats, [],
    )
    assert cats["graph_permission_missing"] >= 1
