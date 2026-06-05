"""
Mela AI - Embed token + config endpoints (Phase 6B).

Embedding apps mint a short-lived JWT for a specific user via
``POST /api/v1/embed/token`` (auth: registered MCP client API key in
``X-Mela-Client-Key``), then load Mela's ``/embed`` page with that
token in the URL.  The page calls ``GET /api/v1/embed/config`` to
discover allowed tools, profile mode, theme overrides, etc.

The embed token is the trust handoff: the embedding app authenticated
the user via its own auth, and presents Mela with a scoped credential
that proves "this user has these permissions for the next hour."
Mela does NOT re-authenticate the user via MSAL.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

from fastapi import APIRouter, Depends, Header, HTTPException, Query, status
from jose import JWTError, jwt
from pydantic import BaseModel, ConfigDict, Field

from app.core.config import settings
from app.core.database import get_db
from app.mcp.auth import authenticate_mcp_client
from app.mcp.tools import MELA_TOOL_NAMES
from app.models.models import MCPClient
from sqlalchemy.ext.asyncio import AsyncSession

logger = logging.getLogger(__name__)
router = APIRouter()


EMBED_TOKEN_TTL_MINUTES = 60
# Distinct ``aud`` value for embed tokens so they can never be confused
# with the user-auth tokens validated by app.core.security.
EMBED_TOKEN_AUDIENCE = "mela-embed"


class EmbedTokenRequest(BaseModel):
    model_config = ConfigDict(extra="ignore")
    user_id: str
    tenant_id: Optional[str] = None
    profile_mode: str = "personal"
    # Optional scope tightening — must be a SUBSET of the MCP client's
    # own scopes.  Empty list inherits the client's full scope.
    allowed_tools: list[str] = Field(default_factory=list)


class EmbedTokenResponse(BaseModel):
    embed_token: str
    expires_at: str
    embed_url: str


# ── Helpers ──────────────────────────────────────────────────────────────


async def _resolve_mcp_client(
    x_mela_client_key: str, db: AsyncSession,
) -> MCPClient:
    """Validate the X-Mela-Client-Key header, return the client row."""
    if not x_mela_client_key:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="X-Mela-Client-Key header required",
        )
    client = await authenticate_mcp_client(x_mela_client_key, db)
    if client is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid client credentials",
        )
    return client


def _mint_embed_token(
    *,
    client: MCPClient,
    user_id: str,
    tenant_id: Optional[str],
    profile_mode: str,
    allowed_tools: list[str],
) -> tuple[str, datetime]:
    """Sign a JWT for the embed surface.  Reuses ``JWT_SECRET_KEY`` —
    same secret signs internal dev tokens.  The audience claim is the
    differentiator: ``mela-embed`` vs the user-auth audience."""
    now = datetime.now(timezone.utc)
    expires_at = now + timedelta(minutes=EMBED_TOKEN_TTL_MINUTES)
    payload = {
        "iss": "mela",
        "aud": EMBED_TOKEN_AUDIENCE,
        "sub": user_id,
        "client_id": client.id,
        "client_name": client.client_name,
        "tenant_id": tenant_id,
        "profile_mode": profile_mode,
        "allowed_tools": allowed_tools,
        "iat": int(now.timestamp()),
        "exp": int(expires_at.timestamp()),
    }
    token = jwt.encode(
        payload, settings.JWT_SECRET_KEY, algorithm=settings.JWT_ALGORITHM,
    )
    return token, expires_at


def _decode_embed_token(token: str) -> dict[str, Any]:
    """Validate + decode an embed token.  Raises 401 on any failure."""
    try:
        return jwt.decode(
            token,
            settings.JWT_SECRET_KEY,
            algorithms=[settings.JWT_ALGORITHM],
            audience=EMBED_TOKEN_AUDIENCE,
        )
    except JWTError as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=f"Invalid embed token: {exc}",
        ) from exc


# ── POST /api/v1/embed/token ─────────────────────────────────────────────


@router.post("/token", response_model=EmbedTokenResponse)
async def mint_embed_token(
    body: EmbedTokenRequest,
    x_mela_client_key: str = Header(default="", alias="X-Mela-Client-Key"),
    db: AsyncSession = Depends(get_db),
) -> EmbedTokenResponse:
    """Issue a one-hour embed token for *body.user_id* on behalf of the
    calling MCP client."""
    client = await _resolve_mcp_client(x_mela_client_key, db)

    user_id = (body.user_id or "").strip()
    if not user_id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="user_id is required",
        )
    profile_mode = body.profile_mode if body.profile_mode in (
        "personal", "work"
    ) else "personal"

    # Scope reconciliation: embed token can't grant tools the client
    # itself doesn't have.  Empty allowed_tools means "inherit".
    client_scopes = set(client.scopes or [])
    if not client_scopes:
        # Defensive — a client with empty scopes shouldn't be able to
        # mint embed tokens that bypass scope checks.
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="client has no tool scopes",
        )

    if body.allowed_tools:
        invalid = [t for t in body.allowed_tools if t not in MELA_TOOL_NAMES]
        if invalid:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"unknown tools: {invalid}",
            )
        if "*" not in client_scopes:
            unauthorised = [
                t for t in body.allowed_tools if t not in client_scopes
            ]
            if unauthorised:
                raise HTTPException(
                    status_code=status.HTTP_403_FORBIDDEN,
                    detail=(
                        "embed token cannot grant tools outside the "
                        f"client's scope: {unauthorised}"
                    ),
                )
        allowed_tools = list(body.allowed_tools)
    else:
        allowed_tools = (
            sorted(MELA_TOOL_NAMES) if "*" in client_scopes
            else sorted(client_scopes)
        )

    token, expires_at = _mint_embed_token(
        client=client,
        user_id=user_id,
        tenant_id=body.tenant_id,
        profile_mode=profile_mode,
        allowed_tools=allowed_tools,
    )
    base = (settings.MELA_INGESTION_BASE_URL or "").strip().rstrip("/")
    embed_url = (
        f"{base}/embed?token={token}" if base else f"/embed?token={token}"
    )
    return EmbedTokenResponse(
        embed_token=token,
        expires_at=expires_at.isoformat(),
        embed_url=embed_url,
    )


# ── GET /api/v1/embed/config ────────────────────────────────────────────


@router.get("/config")
async def embed_config(
    token: str = Query(..., min_length=10),
) -> dict[str, Any]:
    """Return the embed configuration for *token*.

    No auth beyond the embed token itself — the embed page calls this
    on mount with the URL-supplied token.  Expired or tampered tokens
    return 401 (raised inside ``_decode_embed_token``).
    """
    payload = _decode_embed_token(token)
    return {
        "user_id": payload.get("sub"),
        "tenant_id": payload.get("tenant_id"),
        "profile_mode": payload.get("profile_mode") or "personal",
        "allowed_tools": payload.get("allowed_tools") or [],
        "client_id": payload.get("client_id"),
        "client_name": payload.get("client_name"),
        "expires_at": (
            datetime.fromtimestamp(
                int(payload.get("exp") or 0), tz=timezone.utc,
            ).isoformat()
        ),
        # Theme overrides — reserved for future embed customisation.
        # Empty dict for now so the frontend's optional read is safe.
        "theme": {},
    }
