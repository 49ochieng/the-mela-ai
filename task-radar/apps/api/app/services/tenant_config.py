"""Per-tenant Microsoft / integration config.

Stores **only metadata** (azure_tenant_id, azure_client_id,
public-vs-confidential flag, secret reference); the raw client secret
lives exclusively in the secret store. No code path returns the secret
value through an API response or audit log — only the masked reference.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..models import TenantConfig
from .secrets import SecretStore


def _ref_for(tenant_id: str) -> str:
    """Deterministic Key Vault secret name per tenant.

    Vault names allow only ``[A-Za-z0-9-]``; UUID hyphens are already
    allowed. Prefix keeps tenant secrets discoverable in the vault.
    """
    return f"mtr-tenant-{tenant_id}-azure-client-secret"


async def get_config(db: AsyncSession, tenant_id: str) -> TenantConfig | None:
    return (
        await db.execute(select(TenantConfig).where(TenantConfig.tenant_id == tenant_id))
    ).scalar_one_or_none()


async def upsert_config(
    db: AsyncSession,
    *,
    tenant_id: str,
    actor_user_id: str,
    azure_tenant_id: str | None = None,
    azure_client_id: str | None = None,
    azure_client_secret: str | None = None,
    azure_public_client: bool | None = None,
    secret_store: SecretStore,
) -> TenantConfig:
    """Create or update tenant config.

    If ``azure_client_secret`` is provided it is written to the secret store
    under a deterministic, tenant-scoped reference. The plaintext is **not**
    persisted in the database, returned, or logged.
    """
    cfg = await get_config(db, tenant_id)
    if cfg is None:
        cfg = TenantConfig(tenant_id=tenant_id)
        db.add(cfg)

    if azure_tenant_id is not None:
        cfg.azure_tenant_id = azure_tenant_id or None
    if azure_client_id is not None:
        cfg.azure_client_id = azure_client_id or None
    if azure_public_client is not None:
        cfg.azure_public_client = bool(azure_public_client)

    if azure_client_secret is not None:
        if azure_client_secret == "":
            # Explicit clear — remove the secret and the reference.
            if cfg.azure_client_secret_ref:
                await secret_store.delete(cfg.azure_client_secret_ref)
            cfg.azure_client_secret_ref = None
        else:
            ref = cfg.azure_client_secret_ref or _ref_for(tenant_id)
            await secret_store.set(ref, azure_client_secret)
            cfg.azure_client_secret_ref = ref
            cfg.last_rotated_at = datetime.now(timezone.utc).replace(tzinfo=None)

    cfg.updated_by_user_id = actor_user_id
    await db.commit()
    await db.refresh(cfg)
    return cfg


async def resolve_client_secret(
    db: AsyncSession, tenant_id: str, *, secret_store: SecretStore
) -> str | None:
    """Fetch the live client secret for a tenant, or ``None`` if unset.

    This is the **only** place secrets leave the secret store, and the value
    is returned to the in-process MSAL call — never serialised, never logged.
    """
    cfg = await get_config(db, tenant_id)
    if cfg is None or not cfg.azure_client_secret_ref:
        return None
    return await secret_store.get(cfg.azure_client_secret_ref)


def public_view(cfg: TenantConfig | None) -> dict[str, Any]:
    """Safe-to-return projection. Contains no secret material — only a
    boolean flag indicating whether a secret is configured."""
    if cfg is None:
        return {
            "azure_tenant_id": None,
            "azure_client_id": None,
            "azure_public_client": False,
            "has_client_secret": False,
            "last_rotated_at": None,
            "updated_by_user_id": None,
        }
    return {
        "azure_tenant_id": cfg.azure_tenant_id,
        "azure_client_id": cfg.azure_client_id,
        "azure_public_client": bool(cfg.azure_public_client),
        "has_client_secret": bool(cfg.azure_client_secret_ref),
        "last_rotated_at": cfg.last_rotated_at.isoformat() if cfg.last_rotated_at else None,
        "updated_by_user_id": cfg.updated_by_user_id,
    }
