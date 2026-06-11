"""Lock-in tests for the delegated-Graph contract.

These tests use respx to spy on HTTP traffic and assert:
  - All Graph calls go to /me/... (signed-in user only)
  - Bearer token from the user's encrypted token store is used
  - Planner description PATCH sends If-Match: <etag>
  - 4xx errors fail fast (no tenacity retry)
  - 429 / 5xx do retry
  - Excel sync uses /me/drive/...
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import httpx
import pytest
import respx

from app.enums import ConnectionStatus
from app.models import GraphConnection, Tenant, User
from app.services.auth.token_store import StoredToken, get_token_store
from app.services.graph import excel as excel_graph
from app.services.graph import outlook as outlook_graph
from app.services.graph import planner as planner_graph
from app.services.graph.client import GraphClient, GraphHTTPError


async def _make_client(session) -> GraphClient:
    t = Tenant(entra_tenant_id="t1", name="Acme"); session.add(t); await session.flush()
    u = User(tenant_id=t.id, entra_user_id="u1", display_name="A",
             email="a@acme.com", timezone="UTC", role="user")
    session.add(u); await session.flush()
    store = get_token_store()
    ref = store.put("access", StoredToken(
        access_token="USER-DELEGATED-TOKEN", refresh_token=None,
        expires_at=datetime.now(timezone.utc) + timedelta(hours=1), scopes=["Mail.Read"],
    ))
    conn = GraphConnection(
        tenant_id=t.id, user_id=u.id, provider="microsoft",
        scopes="Mail.Read", status=ConnectionStatus.CONNECTED.value,
        token_reference=ref, refresh_token_reference=None,
        expires_at=datetime.now(timezone.utc) + timedelta(hours=1),
    )
    session.add(conn); await session.commit()
    return await GraphClient.for_user(session, u.id, t.id)


@pytest.mark.asyncio
async def test_outlook_uses_me_messages_with_bearer(session):
    client = await _make_client(session)
    try:
        with respx.mock(assert_all_called=True) as rsx:
            route = rsx.get("https://graph.microsoft.com/v1.0/me/messages").mock(
                return_value=httpx.Response(200, json={"value": [{"id": "m1"}]})
            )
            msgs = await outlook_graph.get_messages_since(
                client, datetime(2025, 1, 1, tzinfo=timezone.utc),
            )
            assert msgs == [{"id": "m1"}]
            req = route.calls.last.request
            assert req.headers["Authorization"] == "Bearer USER-DELEGATED-TOKEN"
            assert "/me/messages" in str(req.url)
            # Must NOT call /users/{id}/messages or app-only paths
            assert "/users/" not in str(req.url)
    finally:
        await client.aclose()


@pytest.mark.asyncio
async def test_4xx_fails_fast_without_retry(session):
    """A 404 should raise immediately, not be retried 5x by tenacity."""
    client = await _make_client(session)
    try:
        with respx.mock() as rsx:
            route = rsx.get(
                "https://graph.microsoft.com/v1.0/me/drive/root:/TaskInbox.xlsx"
            ).mock(return_value=httpx.Response(404, text="not found"))
            with pytest.raises(GraphHTTPError) as exc:
                await client.get("/me/drive/root:/TaskInbox.xlsx")
            assert exc.value.status == 404
            assert route.call_count == 1  # no retry
    finally:
        await client.aclose()


@pytest.mark.asyncio
async def test_5xx_is_retried(session):
    client = await _make_client(session)
    try:
        with respx.mock() as rsx:
            route = rsx.get("https://graph.microsoft.com/v1.0/me/messages").mock(
                side_effect=[
                    httpx.Response(503, text="busy"),
                    httpx.Response(503, text="busy"),
                    httpx.Response(200, json={"value": []}),
                ]
            )
            data = await client.get("/me/messages")
            assert data == {"value": []}
            assert route.call_count == 3
    finally:
        await client.aclose()


@pytest.mark.asyncio
async def test_planner_create_sends_if_match_etag(session):
    client = await _make_client(session)
    try:
        with respx.mock() as rsx:
            rsx.post("https://graph.microsoft.com/v1.0/planner/tasks").mock(
                return_value=httpx.Response(201, json={"id": "T1"})
            )
            rsx.get("https://graph.microsoft.com/v1.0/planner/tasks/T1/details").mock(
                return_value=httpx.Response(200, json={"@odata.etag": 'W/"abc123"'})
            )
            patch_route = rsx.patch(
                "https://graph.microsoft.com/v1.0/planner/tasks/T1/details"
            ).mock(return_value=httpx.Response(204))
            await planner_graph.create_task(
                client, plan_id="P1", bucket_id="B1",
                title="Hello", due_date=None, description="Body text",
            )
            assert patch_route.called
            assert patch_route.calls.last.request.headers["If-Match"] == 'W/"abc123"'
    finally:
        await client.aclose()


@pytest.mark.asyncio
async def test_excel_uses_me_drive(session):
    client = await _make_client(session)
    try:
        with respx.mock() as rsx:
            route = rsx.get(
                "https://graph.microsoft.com/v1.0/me/drive/root:/TaskInbox.xlsx"
            ).mock(return_value=httpx.Response(200, json={"id": "wb1", "webUrl": "u"}))
            wb = await excel_graph.find_or_create_task_workbook(client)
            assert wb["id"] == "wb1"
            assert "/me/drive/" in str(route.calls.last.request.url)
    finally:
        await client.aclose()
