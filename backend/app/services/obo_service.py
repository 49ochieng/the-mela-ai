"""
Mela AI — Microsoft Graph Token Service

Provides two token-acquisition flows for Microsoft Graph:

  1. ``get_graph_token_app_only`` — client credentials (app-only). Used by
     background workflows (onboarding, offboarding, SharePoint ingestion)
     that have no interactive user.

  2. ``get_graph_token_obo`` — On-Behalf-Of (delegated). Used by LLM-callable
     tools (send_email, schedule_meeting, etc.) so Microsoft 365 audit logs
     show the real user as the actor, and Graph enforces the user's own
     RBAC/sharing permissions.

OBO requires the enterprise app registration to have the corresponding
**delegated** Graph permissions granted with admin consent (e.g.
``Mail.Send``, ``Calendars.ReadWrite``, ``Files.ReadWrite.All``).

Tokens are cached:
  * App-only — single global cache (one identity).
  * OBO       — per ``(user_oid, scope_hash)`` in Redis when available
                (so cache survives replica restarts and is shared across
                workers); falls back to a process-local dict otherwise.
"""

import asyncio
import hashlib
import json
import logging
import time
from typing import Iterable, List, Optional, Tuple

import msal

from app.core.config import settings

logger = logging.getLogger(__name__)

# ── App-only cache (global) ────────────────────────────────────────────────
_cached_token: Optional[tuple] = None
_TOKEN_TTL_SECONDS = 55 * 60  # 55 minutes (Graph tokens last 60 min)

# ── OBO cache (per user + scope set) ───────────────────────────────────────
# Local fallback when Redis is unavailable.
_obo_local_cache: dict[str, Tuple[str, float]] = {}
_obo_cache_lock = asyncio.Lock()

# Refresh window — re-acquire when fewer than this many seconds remain.
_OBO_REFRESH_BUFFER_S = 300


def _scope_hash(scopes: Iterable[str]) -> str:
    """Stable short hash of a normalised scope set for cache keying."""
    canon = ",".join(sorted({s.strip().lower() for s in scopes if s}))
    return hashlib.sha256(canon.encode("utf-8")).hexdigest()[:16]


def _obo_cache_key(user_oid: str, scopes: Iterable[str]) -> str:
    return f"mela:obo:{user_oid}:{_scope_hash(scopes)}"


async def get_graph_token_app_only() -> Optional[str]:
    """
    Acquire a Microsoft Graph app-only token via client credentials flow.

    Uses AZURE_CLIENT_ID + AZURE_CLIENT_SECRET (enterprise data app).
    Caller must use /users/{email}/... endpoints, not /me/... endpoints.

    Returns:
        A valid Graph access token string, or None if not configured.
    """
    global _cached_token

    client_id = settings.effective_client_id
    client_secret = settings.effective_client_secret
    authority = settings.graph_authority

    if not client_id or not client_secret:
        logger.warning(
            "[Graph] App-only token not configured: "
            "client_id=%s secret_set=%s",
            bool(client_id),
            bool(client_secret),
        )
        return None

    # Return cached token if still valid
    if _cached_token:
        token, expires_at = _cached_token
        if time.monotonic() < expires_at:
            logger.debug("[Graph] Returning cached app-only token")
            return token
        _cached_token = None

    try:
        app = msal.ConfidentialClientApplication(
            client_id=client_id,
            client_credential=client_secret,
            authority=authority,
        )

        result = app.acquire_token_for_client(
            scopes=["https://graph.microsoft.com/.default"]
        )

        if "access_token" in result:
            token = result["access_token"]
            expires_in = result.get("expires_in", _TOKEN_TTL_SECONDS)
            ttl = min(expires_in - 60, _TOKEN_TTL_SECONDS)
            _cached_token = (token, time.monotonic() + ttl)
            logger.info(
                "[Graph] App-only token acquired (expires_in=%ds)",
                expires_in,
            )
            return token

        error = result.get("error", "unknown")
        description = result.get("error_description", "No description")
        logger.warning(
            "[Graph] App-only token failed: error=%s desc=%.120s",
            error,
            description,
        )
        return None

    except Exception as exc:
        logger.error(
            "[Graph] Unexpected error acquiring app-only token: %s",
            exc,
            exc_info=True,
        )
        return None


# ── OBO ────────────────────────────────────────────────────────────────────


def _parse_user_oid_from_assertion(assertion: str) -> Optional[str]:
    """Best-effort decode of the JWT 'oid' claim — used for cache keying.

    We deliberately do **not** verify the signature here; the assertion is
    already validated upstream by ``get_current_user``. We only need a
    stable identifier to scope the OBO cache.
    """
    try:
        parts = assertion.split(".")
        if len(parts) < 2:
            return None
        # Pad base64 to multiple of 4.
        payload_b64 = parts[1] + "=" * (-len(parts[1]) % 4)
        import base64
        payload = json.loads(
            base64.urlsafe_b64decode(payload_b64.encode("utf-8"))
        )
        return (
            payload.get("oid")
            or payload.get("sub")
            or payload.get("preferred_username")
        )
    except Exception:
        return None


async def _read_obo_cache(cache_key: str) -> Optional[str]:
    """Look up a cached OBO token; honour expiry stamp."""
    # Local first.
    entry = _obo_local_cache.get(cache_key)
    if entry:
        tok, exp = entry
        if time.monotonic() < exp:
            return tok
        _obo_local_cache.pop(cache_key, None)

    # Then Redis (shared across replicas).
    try:
        from app.core.redis_client import get_redis
        r = await get_redis()
        if r is None:
            return None
        raw = await r.get(cache_key)
        if not raw:
            return None
        if isinstance(raw, (bytes, bytearray)):
            raw = raw.decode("utf-8")
        return raw
    except Exception as exc:
        logger.debug("[OBO] Redis cache read failed: %s", exc)
        return None


async def _write_obo_cache(
    cache_key: str, token: str, expires_in: int
) -> None:
    ttl = max(60, int(expires_in) - _OBO_REFRESH_BUFFER_S)
    _obo_local_cache[cache_key] = (token, time.monotonic() + ttl)
    try:
        from app.core.redis_client import get_redis
        r = await get_redis()
        if r is None:
            return
        await r.set(cache_key, token, ex=ttl)
    except Exception as exc:
        logger.debug("[OBO] Redis cache write failed: %s", exc)


_DEFAULT_OBO_SCOPES: List[str] = [
    "https://graph.microsoft.com/.default",
]


async def get_graph_token_obo(
    user_assertion: str = "",
    scopes: Optional[List[str]] = None,
    user_oid: Optional[str] = None,
) -> Optional[str]:
    """Acquire a Graph token On-Behalf-Of the signed-in user.

    Args:
        user_assertion: The user's raw bearer token (the access token
            presented to Mela's API). Required for true OBO; if empty or
            the OBO feature flag is off, we transparently fall back to the
            app-only token so existing call sites keep working.
        scopes: Delegated Graph scopes to request (e.g.
            ``["Mail.Send", "Mail.ReadWrite"]``). Defaults to ``.default``.
        user_oid: Optional pre-decoded user object id for cache keying.
            When ``None`` we decode it from ``user_assertion``.

    Returns:
        A valid Graph delegated token string, or ``None`` on failure.
    """
    # Feature flag + assertion gate: when off, behave as before (app-only).
    flag = bool(getattr(settings, "USE_OBO_FOR_GRAPH", False))
    if not flag or not user_assertion:
        if user_assertion and not flag:
            logger.debug(
                "[OBO] USE_OBO_FOR_GRAPH=false — falling back to app-only "
                "despite having a user assertion."
            )
        return await get_graph_token_app_only()

    client_id = settings.effective_client_id
    client_secret = settings.effective_client_secret
    authority = settings.graph_authority
    if not client_id or not client_secret:
        logger.warning("[OBO] Missing client id/secret — cannot acquire OBO.")
        return None

    requested_scopes = list(scopes or _DEFAULT_OBO_SCOPES)
    oid = user_oid or _parse_user_oid_from_assertion(user_assertion) or "anon"
    cache_key = _obo_cache_key(oid, requested_scopes)

    # Serialise concurrent acquisitions for the same key — avoids
    # thundering-herd against MSAL when many tools fire in parallel.
    async with _obo_cache_lock:
        cached = await _read_obo_cache(cache_key)
        if cached:
            return cached

        from app.core.telemetry import start_span
        with start_span(
            "graph.obo.acquire",
            user_oid=oid[:8],
            scope_hash=_scope_hash(requested_scopes),
        ):
            try:
                app = msal.ConfidentialClientApplication(
                    client_id=client_id,
                    client_credential=client_secret,
                    authority=authority,
                )
                # MSAL exposes acquire_token_on_behalf_of synchronously; run in a
                # thread so we don't block the event loop.
                result = await asyncio.to_thread(
                    app.acquire_token_on_behalf_of,
                    user_assertion=user_assertion,
                    scopes=requested_scopes,
                )
            except Exception as exc:
                logger.error(
                    "[OBO] Unexpected MSAL error: %s", exc, exc_info=True
                )
                return None

            if "access_token" in result:
                token = result["access_token"]
                expires_in = int(result.get("expires_in", 3600))
                await _write_obo_cache(cache_key, token, expires_in)
                logger.info(
                    "[OBO] acquired oid=%s scopes=%s expires_in=%ds",
                    oid[:8], _scope_hash(requested_scopes), expires_in,
                )
                return token

            # OBO failure — most common causes are missing delegated permission
            # grant or expired user assertion. Log loudly and return None so
            # the caller can produce a user-facing error.
            error = result.get("error", "unknown")
            description = result.get("error_description", "")[:200]
            logger.warning(
                "[OBO] Token acquisition failed oid=%s error=%s desc=%s",
                oid[:8], error, description,
            )
            return None


def clear_obo_cache() -> None:
    """Clear both token caches (useful for testing)."""
    global _cached_token
    _cached_token = None
    _obo_local_cache.clear()
