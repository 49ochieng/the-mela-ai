"""Admin-only endpoints for managing per-tenant Microsoft credentials.

All routes are protected by :func:`require_admin`. The plaintext client
secret can be **submitted** (write-only) but never **returned** — even to
the admin who set it. To rotate, submit a new value; to clear, submit an
empty string.
"""
from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from ..database import get_session
from ..deps import RequestContext, require_admin
from ..services.secrets import SecretStore, get_secret_store
from ..services.tasks.audit import log as audit_log, verify_chain
from ..services.tenant_config import get_config, public_view, upsert_config

logger = logging.getLogger(__name__)

router = APIRouter()


class TenantConfigPublic(BaseModel):
    azure_tenant_id: str | None = None
    azure_client_id: str | None = None
    azure_public_client: bool = False
    has_client_secret: bool = False
    last_rotated_at: str | None = None
    updated_by_user_id: str | None = None


class TenantConfigUpdate(BaseModel):
    azure_tenant_id: str | None = Field(default=None, max_length=64)
    azure_client_id: str | None = Field(default=None, max_length=64)
    # Empty string is a sentinel meaning "clear the secret".
    azure_client_secret: str | None = Field(default=None, max_length=512)
    azure_public_client: bool | None = None


def _store() -> SecretStore:
    return get_secret_store()


@router.get("/admin/tenant-config", response_model=TenantConfigPublic)
async def read_tenant_config(
    ctx: RequestContext = Depends(require_admin),
    session: AsyncSession = Depends(get_session),
) -> TenantConfigPublic:
    cfg = await get_config(session, ctx.tenant_id)
    return TenantConfigPublic(**public_view(cfg))


@router.put("/admin/tenant-config", response_model=TenantConfigPublic)
async def update_tenant_config(
    body: TenantConfigUpdate,
    ctx: RequestContext = Depends(require_admin),
    session: AsyncSession = Depends(get_session),
    store: SecretStore = Depends(_store),
) -> TenantConfigPublic:
    cfg = await upsert_config(
        session,
        tenant_id=ctx.tenant_id,
        actor_user_id=ctx.user.id,
        azure_tenant_id=body.azure_tenant_id,
        azure_client_id=body.azure_client_id,
        azure_client_secret=body.azure_client_secret,
        azure_public_client=body.azure_public_client,
        secret_store=store,
    )
    # Audit only metadata fields and a coarse "secret changed" boolean —
    # never the secret itself or its reference.
    await audit_log(
        session,
        tenant_id=ctx.tenant_id,
        user_id=ctx.user.id,
        action="tenant_config.update",
        entity_type="tenant_config",
        entity_id=cfg.id,
        details={
            "azure_tenant_id_set": bool(cfg.azure_tenant_id),
            "azure_client_id_set": bool(cfg.azure_client_id),
            "azure_public_client": bool(cfg.azure_public_client),
            "secret_changed": body.azure_client_secret is not None,
        },
    )
    await session.commit()
    return TenantConfigPublic(**public_view(cfg))


@router.delete(
    "/admin/tenant-config/secret",
    status_code=status.HTTP_204_NO_CONTENT,
)
async def clear_tenant_secret(
    ctx: RequestContext = Depends(require_admin),
    session: AsyncSession = Depends(get_session),
    store: SecretStore = Depends(_store),
) -> None:
    cfg = await get_config(session, ctx.tenant_id)
    if cfg is None or not cfg.azure_client_secret_ref:
        return
    await upsert_config(
        session,
        tenant_id=ctx.tenant_id,
        actor_user_id=ctx.user.id,
        azure_client_secret="",
        secret_store=store,
    )
    await audit_log(
        session,
        tenant_id=ctx.tenant_id,
        user_id=ctx.user.id,
        action="tenant_config.secret_cleared",
        entity_type="tenant_config",
        entity_id=cfg.id,
    )
    await session.commit()
    return None


@router.post("/admin/tenant-config/test")
async def test_tenant_config(
    ctx: RequestContext = Depends(require_admin),
    session: AsyncSession = Depends(get_session),
    store: SecretStore = Depends(_store),
) -> dict[str, object]:
    """Validate the saved config without exposing secret material.

    Returns a structured boolean report. We do **not** attempt a live MSAL
    handshake here (that would risk leaking provider error text into the
    response); instead we verify the local invariants and that the secret
    is actually retrievable from the store.
    """
    cfg = await get_config(session, ctx.tenant_id)
    if cfg is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Tenant config not set")
    secret_ok = True
    if cfg.azure_client_secret_ref:
        try:
            value = await store.get(cfg.azure_client_secret_ref)
            secret_ok = bool(value)
        except Exception:  # pragma: no cover
            secret_ok = False
    return {
        "tenant_id_set": bool(cfg.azure_tenant_id),
        "client_id_set": bool(cfg.azure_client_id),
        "secret_resolvable": secret_ok,
        "public_client": bool(cfg.azure_public_client),
    }


@router.get("/admin/audit/verify")
async def verify_audit_chain(
    ctx: RequestContext = Depends(require_admin),
    session: AsyncSession = Depends(get_session),
) -> dict[str, object]:
    """Replay the audit hash chain and report tampering, if any."""
    return await verify_chain(session)
