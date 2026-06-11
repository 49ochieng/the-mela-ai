"""Privacy / GDPR endpoints.

* ``GET /api/me/export`` returns a JSON bundle of every row owned by the
  signed-in user across all per-user tables. The bundle excludes secret
  material (token references, hashed agent tokens) — only data the user
  themselves provided or generated through normal use.
* ``POST /api/me/delete`` schedules account erasure: revokes all sessions
  and agent tokens immediately, marks the Graph connection disconnected,
  and stamps the user with ``deletion_requested_at``. The
  ``app.workers.account_deleter`` background job (run by the scheduler
  process daily at 03:00 UTC) hard-deletes every per-user row after the
  configured grace window has elapsed.

The hard-delete worker is intentionally NOT in this router — keeping
deletion eventually-consistent gives the user a window to cancel and
prevents an attacker who stole a session from instantly nuking the
account.
"""
from __future__ import annotations

import logging
from datetime import datetime
from typing import Any

from fastapi import APIRouter, Depends, status
from pydantic import BaseModel
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from ..database import get_session
from ..deps import RequestContext, get_current_user
from ..models import (
    AgentToken,
    GraphConnection,
    ScanEvent,
    ScanRun,
    ScanSettings,
    Session as SessionModel,
    SourceMessage,
    Task,
    TaskAttachment,
    TaskSync,
    User,
)
from ..services.tasks.audit import log as audit_log

logger = logging.getLogger(__name__)
router = APIRouter()


_PER_USER_TABLES = (
    ("scan_settings", ScanSettings),
    ("scan_runs", ScanRun),
    ("scan_events", ScanEvent),
    ("source_messages", SourceMessage),
    ("tasks", Task),
    ("task_attachments", TaskAttachment),
    ("task_syncs", TaskSync),
)


def _row_to_dict(row: Any) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for col in row.__table__.columns:
        v = getattr(row, col.name)
        if isinstance(v, datetime):
            out[col.name] = v.isoformat()
        else:
            out[col.name] = v
    return out


@router.get("/me/export")
async def export_my_data(
    ctx: RequestContext = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    """Return every row this user owns across per-user tables.

    Secret-bearing fields (token references, hashed agent tokens) are
    intentionally omitted — exporting them would defeat the encryption.
    """
    bundle: dict[str, Any] = {
        "exported_at": datetime.utcnow().isoformat() + "Z",
        "user": {
            "id": ctx.user.id,
            "tenant_id": ctx.user.tenant_id,
            "email": ctx.user.email,
            "display_name": ctx.user.display_name,
            "role": ctx.user.role,
            "timezone": ctx.user.timezone,
        },
    }
    for label, model in _PER_USER_TABLES:
        rows = (
            await session.execute(
                select(model).where(
                    model.tenant_id == ctx.tenant_id,
                    model.user_id == ctx.user.id,
                )
            )
        ).scalars().all()
        bundle[label] = [_row_to_dict(r) for r in rows]
    # Connections — strip token refs.
    conns = (
        await session.execute(
            select(GraphConnection).where(
                GraphConnection.tenant_id == ctx.tenant_id,
                GraphConnection.user_id == ctx.user.id,
            )
        )
    ).scalars().all()
    bundle["graph_connections"] = [
        {k: v for k, v in _row_to_dict(c).items()
         if k not in {"token_reference", "refresh_token_reference"}}
        for c in conns
    ]
    # Agent tokens — metadata only, never the hash.
    tokens = (
        await session.execute(
            select(AgentToken).where(
                AgentToken.tenant_id == ctx.tenant_id,
                AgentToken.user_id == ctx.user.id,
            )
        )
    ).scalars().all()
    bundle["agent_tokens"] = [
        {k: v for k, v in _row_to_dict(t).items() if k != "token_hash"}
        for t in tokens
    ]
    await audit_log(
        session,
        tenant_id=ctx.tenant_id,
        user_id=ctx.user.id,
        action="me.export",
        details={"tables": [label for label, _ in _PER_USER_TABLES]},
    )
    await session.commit()
    return bundle


class DeleteRequestResult(BaseModel):
    status: str
    sessions_revoked: int
    agent_tokens_revoked: int
    connections_disconnected: int
    grace_period_days: int


@router.post("/me/delete", response_model=DeleteRequestResult)
async def request_account_deletion(
    ctx: RequestContext = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> DeleteRequestResult:
    """Soft-delete: revoke access immediately, hard-delete after grace."""
    now = datetime.utcnow()
    grace_days = 30

    # Revoke all sessions for this user.
    sess_res = await session.execute(
        update(SessionModel)
        .where(
            SessionModel.user_id == ctx.user.id,
            SessionModel.tenant_id == ctx.tenant_id,
            SessionModel.revoked_at.is_(None),
        )
        .values(revoked_at=now)
    )
    # Revoke all agent tokens.
    tok_res = await session.execute(
        update(AgentToken)
        .where(
            AgentToken.user_id == ctx.user.id,
            AgentToken.tenant_id == ctx.tenant_id,
            AgentToken.revoked_at.is_(None),
        )
        .values(revoked_at=now)
    )
    # Mark Graph connections disconnected and clear token references so the
    # encrypted refresh token is no longer usable from this row even if the
    # delete is later cancelled (user must reconnect).
    conn_res = await session.execute(
        update(GraphConnection)
        .where(
            GraphConnection.user_id == ctx.user.id,
            GraphConnection.tenant_id == ctx.tenant_id,
        )
        .values(status="disconnected", token_reference=None,
                refresh_token_reference=None)
    )
    # Stamp the user so account_deleter can hard-delete after the grace
    # window. Keep the existing value if the user re-requests deletion
    # (the original timestamp anchors the grace window).
    await session.execute(
        update(User)
        .where(
            User.id == ctx.user.id,
            User.tenant_id == ctx.tenant_id,
            User.deletion_requested_at.is_(None),
        )
        .values(deletion_requested_at=now)
    )
    await audit_log(
        session,
        tenant_id=ctx.tenant_id,
        user_id=ctx.user.id,
        action="me.delete_requested",
        details={
            "grace_period_days": grace_days,
            "sessions_revoked": sess_res.rowcount or 0,
            "agent_tokens_revoked": tok_res.rowcount or 0,
            "connections_disconnected": conn_res.rowcount or 0,
        },
    )
    await session.commit()
    return DeleteRequestResult(
        status="scheduled",
        sessions_revoked=sess_res.rowcount or 0,
        agent_tokens_revoked=tok_res.rowcount or 0,
        connections_disconnected=conn_res.rowcount or 0,
        grace_period_days=grace_days,
    )
