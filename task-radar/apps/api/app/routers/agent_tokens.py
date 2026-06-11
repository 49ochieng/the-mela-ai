"""Per-user agent tokens — issued, listed, revoked by the signed-in user.

A token is a random 32-byte url-safe string prefixed with ``mtr_at_``. The
plaintext value is shown to the user **once** at creation time and never
stored. Only the SHA-256 hash is persisted, so a leaked DB row cannot be
replayed.

Mela / MCP / external agents authenticate as the user that issued the token
by sending ``Authorization: Bearer mtr_at_<value>``.
"""
from __future__ import annotations

import logging
import secrets
from datetime import datetime, timedelta

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..database import get_session
from ..deps import AGENT_TOKEN_PREFIX, RequestContext, get_current_user, hash_agent_token
from ..models import AgentToken
from ..services.tasks.audit import log

logger = logging.getLogger("app.agent_tokens")

router = APIRouter()

# Hard upper bound regardless of what the client sends. Keeps the blast radius
# of a leaked token bounded even if the user picks a long expiry by mistake.
MAX_EXPIRES_DAYS = 365
DEFAULT_EXPIRES_DAYS = 90


class AgentTokenCreate(BaseModel):
    name: str = Field(min_length=1, max_length=128)
    expires_days: int = Field(default=DEFAULT_EXPIRES_DAYS, ge=1, le=MAX_EXPIRES_DAYS)


class AgentTokenInfo(BaseModel):
    id: str
    name: str
    created_at: datetime
    expires_at: datetime | None
    last_used_at: datetime | None
    revoked_at: datetime | None


class AgentTokenCreated(AgentTokenInfo):
    token: str  # plaintext — shown once


def _to_info(row: AgentToken) -> AgentTokenInfo:
    return AgentTokenInfo(
        id=row.id,
        name=row.name,
        created_at=row.created_at,
        expires_at=row.expires_at,
        last_used_at=row.last_used_at,
        revoked_at=row.revoked_at,
    )


@router.post("/agent-tokens", response_model=AgentTokenCreated, status_code=201)
async def create_agent_token(
    payload: AgentTokenCreate,
    ctx: RequestContext = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> AgentTokenCreated:
    # Only browser sessions can mint new tokens. An agent token cannot mint
    # another agent token — that would defeat revocation.
    if ctx.auth_method != "session":
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Agent tokens may only be issued from a signed-in browser session")

    plaintext = AGENT_TOKEN_PREFIX + secrets.token_urlsafe(32)
    row = AgentToken(
        tenant_id=ctx.tenant_id,
        user_id=ctx.user_id,
        name=payload.name.strip()[:128],
        token_hash=hash_agent_token(plaintext),
        expires_at=datetime.utcnow() + timedelta(days=payload.expires_days),
    )
    session.add(row)
    await session.flush()
    await log(session, tenant_id=ctx.tenant_id, user_id=ctx.user_id,
              action="agent_token.created", entity_type="agent_token", entity_id=row.id)
    await session.commit()
    logger.info("agent_token.created user_id=%s id=%s expires=%s",
                ctx.user_id, row.id, row.expires_at.isoformat())
    return AgentTokenCreated(**_to_info(row).model_dump(), token=plaintext)


@router.get("/agent-tokens", response_model=list[AgentTokenInfo])
async def list_agent_tokens(
    ctx: RequestContext = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> list[AgentTokenInfo]:
    rows = (
        await session.execute(
            select(AgentToken).where(
                AgentToken.user_id == ctx.user_id,
                AgentToken.tenant_id == ctx.tenant_id,
            ).order_by(AgentToken.created_at.desc())
        )
    ).scalars().all()
    return [_to_info(r) for r in rows]


@router.delete("/agent-tokens/{token_id}", status_code=204)
async def revoke_agent_token(
    token_id: str,
    ctx: RequestContext = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> None:
    row = await session.get(AgentToken, token_id)
    if (
        row is None
        or row.user_id != ctx.user_id
        or row.tenant_id != ctx.tenant_id
    ):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Token not found")
    if row.revoked_at is None:
        row.revoked_at = datetime.utcnow()
        await log(session, tenant_id=ctx.tenant_id, user_id=ctx.user_id,
                  action="agent_token.revoked", entity_type="agent_token", entity_id=row.id)
        await session.commit()
        logger.info("agent_token.revoked user_id=%s id=%s", ctx.user_id, row.id)
