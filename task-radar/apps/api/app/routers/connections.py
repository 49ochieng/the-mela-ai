"""Connection status + disconnect."""
from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..config import get_settings
from ..database import get_session
from ..deps import RequestContext, get_current_user
from ..enums import ConnectionStatus
from ..models import GraphConnection
from ..schemas import ConnectionInfo
from ..services.graph import teams as teams_svc
from ..services.graph.client import GraphClient, GraphHTTPError, NeedsReconnect
from ..services.tasks.audit import log

router = APIRouter()


@router.get("/connections")
async def list_connections(
    ctx: RequestContext = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> dict:
    res = await session.execute(
        select(GraphConnection).where(
            GraphConnection.user_id == ctx.user_id,
            GraphConnection.tenant_id == ctx.tenant_id,
        )
    )
    conns = res.scalars().all()
    return {"items": [
        ConnectionInfo(
            provider=c.provider, status=ConnectionStatus(c.status),
            scopes=c.scopes.split() if c.scopes else [],
            last_connected_at=c.last_connected_at, expires_at=c.expires_at,
        )
        for c in conns
    ]}


@router.post("/connections/microsoft/connect")
async def connect_microsoft() -> dict:
    return {"redirect": "/api/auth/microsoft/login"}


@router.post("/connections/microsoft/disconnect")
async def disconnect_microsoft(
    ctx: RequestContext = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> dict:
    res = await session.execute(
        select(GraphConnection).where(
            GraphConnection.user_id == ctx.user_id,
            GraphConnection.tenant_id == ctx.tenant_id,
            GraphConnection.provider == "microsoft",
        )
    )
    conn = res.scalar_one_or_none()
    if conn:
        conn.status = ConnectionStatus.DISCONNECTED.value
        conn.token_reference = None
        conn.refresh_token_reference = None
        await log(session, tenant_id=ctx.tenant_id, user_id=ctx.user_id,
                  action="auth.microsoft.disconnected")
        await session.commit()
    return {"ok": True}


@router.get("/connections/permissions")
async def permissions() -> dict:
    s = get_settings()
    return {"scopes": s.graph_scope_list}


@router.get("/connections/teams/joined")
async def list_joined_teams(
    ctx: RequestContext = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> dict:
    try:
        client = await GraphClient.for_user(session, ctx.user_id, ctx.tenant_id)
    except NeedsReconnect as exc:
        return {"items": [], "error": "needs_reconnect", "message": str(exc)}
    try:
        teams = await teams_svc.list_joined_teams(client)
    except GraphHTTPError as exc:
        return {"items": [], "error": f"graph_{exc.status}", "message": str(exc)[:300]}
    finally:
        await client.aclose()
    return {"items": [
        {"id": t.get("id"), "displayName": t.get("displayName"),
         "description": t.get("description")}
        for t in (teams or [])
    ]}


@router.get("/connections/teams/chats")
async def list_my_chats(
    ctx: RequestContext = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> dict:
    """Debug: list recent chats and a sample of their latest messages.
    Available only when APP_ENV=development to avoid leaking message previews
    in production logs/responses."""
    if get_settings().app_env != "development":
        from fastapi import HTTPException
        raise HTTPException(404, "Not found")
    try:
        client = await GraphClient.for_user(session, ctx.user_id, ctx.tenant_id)
    except NeedsReconnect as exc:
        return {"chats": [], "error": "needs_reconnect", "message": str(exc)}
    try:
        from datetime import datetime, timedelta
        chats = await teams_svc.list_chats(client)
        since = datetime.utcnow() - timedelta(days=7)
        result = []
        for chat in chats[:5]:  # preview first 5 chats
            chat_id = chat.get("id")
            chat_type = chat.get("chatType", "unknown")
            label = (chat.get("topic") or "").strip() or chat_type
            msgs = []
            try:
                raw = await teams_svc.get_chat_messages_since(client, chat_id, since)
                msgs = [
                    {
                        "id": m.get("id"),
                        "from": (m.get("from") or {}).get("user", {}).get("displayName"),
                        "preview": (m.get("body") or {}).get("content", "")[:100],
                        "messageType": m.get("messageType"),
                        "createdDateTime": m.get("createdDateTime"),
                    }
                    for m in raw[:3]
                    if m.get("messageType") in (None, "message")
                ]
            except GraphHTTPError as exc:
                msgs = [{"error": f"graph_{exc.status}"}]
            result.append({
                "id": chat_id,
                "chatType": chat_type,
                "label": label,
                "recentMessages": msgs,
            })
        return {"chats_total": len(chats), "preview": result}
    except GraphHTTPError as exc:
        return {"chats": [], "error": f"graph_{exc.status}", "message": str(exc)[:300]}
    finally:
        await client.aclose()


@router.get("/connections/teams/{team_id}/channels")
async def list_team_channels(
    team_id: str,
    ctx: RequestContext = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> dict:
    try:
        client = await GraphClient.for_user(session, ctx.user_id, ctx.tenant_id)
    except NeedsReconnect as exc:
        return {"items": [], "error": "needs_reconnect", "message": str(exc)}
    try:
        channels = await teams_svc.list_channels(client, team_id)
    except GraphHTTPError as exc:
        return {"items": [], "error": f"graph_{exc.status}", "message": str(exc)[:300]}
    finally:
        await client.aclose()
    return {"items": [
        {"id": c.get("id"), "displayName": c.get("displayName"),
         "membershipType": c.get("membershipType")}
        for c in (channels or [])
    ]}
