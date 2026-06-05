"""
Mela AI - MCP client authentication (Phase 6A).

External MCP clients present an ``X-Api-Key`` header.  We bcrypt-hash
the key on storage and verify with ``bcrypt.checkpw``.  Validation is
linear in the number of *active* clients (not all clients) — the
revoked-at filter prunes the candidate set first.

Hard rules:
  * Never log the plaintext key.  Never store it.
  * Plaintext is generated at create time and returned exactly once
    via the admin endpoint; if the client loses it, admins must
    revoke and recreate.
  * Constant-time compare via ``bcrypt.checkpw`` is built in.
  * ``last_used_at`` is updated on each successful auth — best-effort,
    never blocks the request on persistence failure.
"""

from __future__ import annotations

import logging
import secrets
from datetime import datetime
from typing import Optional

import bcrypt
from fastapi import Depends, Header, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.mcp.tools import is_tool_in_scope
from app.models.models import MCPClient

logger = logging.getLogger(__name__)


# Plaintext keys are ``mela_<32 url-safe chars>`` — the prefix makes
# leaked keys identifiable in logs for incident response.
KEY_PREFIX = "mela_"
KEY_LENGTH_BYTES = 24  # → 32 url-safe base64 chars after secrets.token_urlsafe


def generate_api_key() -> str:
    """Generate a fresh plaintext key for a new MCP client."""
    return KEY_PREFIX + secrets.token_urlsafe(KEY_LENGTH_BYTES)


def hash_api_key(plaintext: str) -> str:
    """bcrypt-hash a plaintext key, returning the encoded string."""
    return bcrypt.hashpw(plaintext.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")


def verify_api_key(plaintext: str, hashed: str) -> bool:
    """Constant-time compare — never raises."""
    try:
        return bcrypt.checkpw(plaintext.encode("utf-8"), hashed.encode("utf-8"))
    except Exception:
        return False


async def authenticate_mcp_client(
    api_key: str, db: AsyncSession
) -> Optional[MCPClient]:
    """Resolve *api_key* to its MCPClient row, or None on no match.

    Iterates over active (non-revoked) clients and verifies bcrypt-
    hashed keys.  Active-client count is expected to be small (tens,
    not millions); add a key-prefix index if it ever grows.
    """
    if not api_key:
        return None
    try:
        rows = (
            await db.execute(
                select(MCPClient).where(MCPClient.revoked_at.is_(None))
            )
        ).scalars().all()
    except Exception as exc:  # noqa: BLE001
        logger.warning("MCP auth DB lookup failed: %s", exc)
        return None
    for client in rows:
        if verify_api_key(api_key, client.api_key_hash):
            return client
    return None


async def require_mcp_client(
    x_api_key: str = Header(..., alias="X-Api-Key"),
    db: AsyncSession = Depends(get_db),
) -> MCPClient:
    """FastAPI dependency: validate the inbound MCP client key.

    Returns the matched :class:`MCPClient` so handlers can read
    ``client.scopes`` for per-tool authorisation without a second
    lookup.

    Raises HTTP 401 on every failure mode (no key, unknown key,
    revoked key) — never reveal which.
    """
    if not x_api_key:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="MCP authentication required",
        )
    client = await authenticate_mcp_client(x_api_key, db)
    if client is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid MCP credentials",
        )

    # Best-effort last-used touch — non-blocking, swallows persistence
    # failures so a transient DB blip never 500s an MCP call.
    try:
        client.last_used_at = datetime.utcnow()
        await db.commit()
    except Exception as exc:  # noqa: BLE001
        logger.debug("MCP last_used_at update skipped: %s", exc)

    return client


def assert_tool_scope(client: MCPClient, tool_name: str) -> None:
    """Raise 403 if the client's scopes do not permit *tool_name*."""
    if not is_tool_in_scope(tool_name, client.scopes or []):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=f"client scope does not include tool {tool_name!r}",
        )
