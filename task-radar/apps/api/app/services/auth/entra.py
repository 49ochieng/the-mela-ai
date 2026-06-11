"""Microsoft Entra OAuth (authorization code flow) using MSAL.

Supports two app-registration modes:
  * Confidential client (default) — uses ``client_secret``.
  * Public client (``AZURE_PUBLIC_CLIENT=true`` or no ``AZURE_CLIENT_SECRET``)
    — uses PKCE only. Required when the Azure App Registration is configured
    as a mobile/desktop client or has "Allow public client flows" enabled.

Per-tenant overrides (Phase 3): once an admin saves a TenantConfig with a
client_id/secret reference, callers that already know the tenant_id can
build an MSAL app from that config via :func:`msal_app_for_tenant`. The
plaintext secret is fetched from the secret store at call-time and is
never persisted in this module.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any, Union

import msal

from ...config import get_settings


def _is_public_client() -> bool:
    s = get_settings()
    return bool(s.azure_public_client) or not s.azure_client_secret


def _msal_app() -> Union[msal.ConfidentialClientApplication, msal.PublicClientApplication]:
    s = get_settings()
    authority = f"https://login.microsoftonline.com/{s.azure_tenant_id or 'common'}"
    if _is_public_client():
        return msal.PublicClientApplication(
            client_id=s.azure_client_id,
            authority=authority,
        )
    return msal.ConfidentialClientApplication(
        client_id=s.azure_client_id,
        client_credential=s.azure_client_secret,
        authority=authority,
    )


def initiate_auth_code_flow(redirect_uri: str | None = None) -> dict[str, Any]:
    """Start PKCE authorization code flow. Returns the full flow dict which
    must be stored server-side (keyed by flow['state']) and passed to
    acquire_token_by_auth_code_flow on the callback."""
    s = get_settings()
    scopes = [sc for sc in s.graph_scope_list if sc not in {"openid", "profile", "offline_access"}]
    flow = _msal_app().initiate_auth_code_flow(
        scopes=scopes,
        redirect_uri=redirect_uri or s.microsoft_redirect_uri,
        prompt="select_account",
    )
    if "error" in flow:
        raise RuntimeError(f"Failed to initiate auth flow: {flow}")
    return flow


def acquire_token_by_auth_code_flow(auth_flow: dict[str, Any], auth_response: dict[str, Any]) -> dict[str, Any]:
    """Exchange the callback parameters for tokens using the stored PKCE flow."""
    result = _msal_app().acquire_token_by_auth_code_flow(
        auth_code_flow=auth_flow,
        auth_response=auth_response,
    )
    if "access_token" not in result:
        raise RuntimeError(f"OAuth failure: {result.get('error_description') or result}")
    return result


def acquire_token_by_refresh(refresh_token: str) -> dict[str, Any]:
    s = get_settings()
    result = _msal_app().acquire_token_by_refresh_token(
        refresh_token=refresh_token,
        scopes=[sc for sc in s.graph_scope_list if sc not in {"openid", "profile", "offline_access"}],
    )
    if "access_token" not in result:
        raise RuntimeError(f"Refresh failure: {result.get('error_description') or result}")
    return result


def expires_at_from(result: dict[str, Any]) -> datetime:
    return datetime.now(timezone.utc) + timedelta(seconds=int(result.get("expires_in", 3600)))


async def msal_app_for_tenant(db, tenant_id: str):
    """Build an MSAL app using per-tenant config when available.

    Falls back to the global env-configured app if the tenant has no saved
    overrides yet. The secret is fetched from the secret store on every
    call — no plaintext is cached on the model or in this module.
    """
    from ..secrets import get_secret_store
    from ..tenant_config import get_config, resolve_client_secret

    cfg = await get_config(db, tenant_id)
    if cfg is None or not cfg.azure_client_id:
        return _msal_app()
    authority = f"https://login.microsoftonline.com/{cfg.azure_tenant_id or 'common'}"
    if cfg.azure_public_client or not cfg.azure_client_secret_ref:
        return msal.PublicClientApplication(
            client_id=cfg.azure_client_id, authority=authority,
        )
    secret = await resolve_client_secret(db, tenant_id, secret_store=get_secret_store())
    if not secret:
        return _msal_app()
    return msal.ConfidentialClientApplication(
        client_id=cfg.azure_client_id,
        client_credential=secret,
        authority=authority,
    )
