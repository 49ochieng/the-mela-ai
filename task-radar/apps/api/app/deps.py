"""Request-scoped dependencies (current user, db session, etc.)."""
from __future__ import annotations

import hashlib
from dataclasses import dataclass
from datetime import datetime

from fastapi import Cookie, Depends, Header, HTTPException, Request, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from .config import get_settings
from .database import get_session
from .models import AgentToken, User
from .utils.jwt import decode_session_token

# Plain-text agent tokens are presented as: "mtr_at_<base64url-random>".
# This prefix lets the auth layer fast-path between JWT sessions and
# revocable per-user agent tokens without ambiguity.
AGENT_TOKEN_PREFIX = "mtr_at_"


@dataclass
class RequestContext:
    user: User
    tenant_id: str
    auth_method: str = "session"  # "session" | "agent_token"
    agent_token_id: str | None = None

    @property
    def user_id(self) -> str:
        return self.user.id


def hash_agent_token(plain: str) -> str:
    return hashlib.sha256(plain.encode("utf-8")).hexdigest()


async def _resolve_agent_token(session: AsyncSession, plain: str) -> RequestContext:
    h = hash_agent_token(plain)
    row = (
        await session.execute(select(AgentToken).where(AgentToken.token_hash == h))
    ).scalar_one_or_none()
    if row is None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid agent token")
    if row.revoked_at is not None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Agent token revoked")
    if row.expires_at is not None and row.expires_at < datetime.utcnow():
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Agent token expired")
    user = await session.get(User, row.user_id)
    if user is None or user.tenant_id != row.tenant_id:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "User not found")
    row.last_used_at = datetime.utcnow()
    await session.commit()
    return RequestContext(
        user=user,
        tenant_id=user.tenant_id,
        auth_method="agent_token",
        agent_token_id=row.id,
    )


async def get_current_user(
    request: Request,
    authorization: str | None = Header(default=None),
    mtr_session: str | None = Cookie(default=None, alias="mtr_session"),
    session: AsyncSession = Depends(get_session),
) -> RequestContext:
    """Resolve the calling principal.

    Order:
      1. Session cookie (browser).
      2. ``Authorization: Bearer mtr_at_...`` — per-user, revocable agent token.
      3. ``Authorization: Bearer <jwt>`` — programmatic session JWT.

    There is **no** shared service key path; Mela / MCP / external agents must
    use a per-user agent token issued through the UI by the signed-in user.
    """
    bearer: str | None = None
    if authorization and authorization.lower().startswith("bearer "):
        bearer = authorization.split(" ", 1)[1].strip()

    # Path 2: agent token (checked before JWT decode because it is not a JWT)
    if bearer and bearer.startswith(AGENT_TOKEN_PREFIX):
        return await _resolve_agent_token(session, bearer)

    # Path 1 / 3: JWT session (cookie preferred over bearer). When the deploy
    # uses the ``__Host-`` cookie prefix the legacy ``mtr_session`` alias
    # won't match, so also probe the effective name from settings.
    settings = get_settings()
    cookie_val = mtr_session or request.cookies.get(settings.effective_cookie_name)
    token = cookie_val or bearer
    if not token:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Not authenticated")
    try:
        payload = decode_session_token(token)
    except ValueError as e:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, str(e)) from e
    # Server-side revocation check: the JWT must be paired with an active
    # row in the sessions table. If the row is missing, revoked, or expired,
    # reject — even if the JWT itself is still cryptographically valid.
    jti = payload.get("jti")
    if jti:
        from .services.auth.sessions import get_active_session
        srow = await get_active_session(session, jti)
        if srow is None:
            raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Session revoked or expired")
    user = await session.get(User, payload["sub"])
    if user is None or user.tenant_id != payload["tid"]:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "User not found")
    return RequestContext(user=user, tenant_id=user.tenant_id, auth_method="session")


async def require_admin(
    ctx: RequestContext = Depends(get_current_user),
) -> RequestContext:
    if (ctx.user.role or "user") != "admin":
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Admin role required")
    return ctx

