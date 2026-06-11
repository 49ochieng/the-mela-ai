"""Admin security operations: emergency lockdown.

The lockdown endpoint is the human-operator equivalent of the emergency
brake. When invoked it:

  * revokes every active session (every JWT immediately stops working),
  * revokes every active agent token (Mela / MCP / external automation
    can no longer act on behalf of any user),
  * clears every Graph connection's cached token references (subsequent
    calls force a fresh OAuth flow),
  * writes a single audit row that incident-response can pivot from.

It does **not** delete data. Recovery is a re-login by each user; admins
can restore Graph connections by re-running consent.
"""
from __future__ import annotations

import logging
from datetime import datetime

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy import update
from sqlalchemy.ext.asyncio import AsyncSession

from ..database import get_session
from ..deps import RequestContext, require_admin
from ..models import AgentToken, GraphConnection, Session as SessionModel
from ..services.tasks.audit import log as audit_log

logger = logging.getLogger(__name__)
router = APIRouter()


class LockdownResult(BaseModel):
    sessions_revoked: int
    agent_tokens_revoked: int
    graph_connections_disconnected: int
    timestamp: str


@router.post("/admin/security/lockdown", response_model=LockdownResult)
async def emergency_lockdown(
    ctx: RequestContext = Depends(require_admin),
    session: AsyncSession = Depends(get_session),
) -> LockdownResult:
    now = datetime.utcnow()
    sess_res = await session.execute(
        update(SessionModel)
        .where(SessionModel.revoked_at.is_(None))
        .values(revoked_at=now)
    )
    tok_res = await session.execute(
        update(AgentToken)
        .where(AgentToken.revoked_at.is_(None))
        .values(revoked_at=now)
    )
    conn_res = await session.execute(
        update(GraphConnection)
        .where(GraphConnection.status != "disconnected")
        .values(status="disconnected", token_reference=None,
                refresh_token_reference=None)
    )
    await audit_log(
        session,
        tenant_id=ctx.tenant_id,
        user_id=ctx.user.id,
        action="security.lockdown",
        details={
            "sessions_revoked": sess_res.rowcount or 0,
            "agent_tokens_revoked": tok_res.rowcount or 0,
            "connections_disconnected": conn_res.rowcount or 0,
        },
    )
    await session.commit()
    logger.warning(
        "Emergency lockdown invoked by admin %s — all sessions and tokens revoked.",
        ctx.user.id,
    )
    return LockdownResult(
        sessions_revoked=sess_res.rowcount or 0,
        agent_tokens_revoked=tok_res.rowcount or 0,
        graph_connections_disconnected=conn_res.rowcount or 0,
        timestamp=now.isoformat() + "Z",
    )
