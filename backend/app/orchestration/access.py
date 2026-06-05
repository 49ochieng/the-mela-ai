"""
Mela AI - Per-tenant worker access (Phase 5C).

Single source of truth for "can tenant T invoke worker W?".  Two
callers — ``tool_bridge.synth_worker_tools`` (filters tools the LLM
ever sees) and ``Router.route`` (defence in depth).

Default-allow mode
------------------

When ``settings.WORKER_ACCESS_DEFAULT_ALLOW`` is True (the default)
``has_access`` and ``allowed_workers`` short-circuit to True / pass-
through without touching the DB.  Existing deployments see zero
behavioural change.

Default-deny mode
-----------------

When the flag is False, a tenant needs a row in
``worker_tenant_access`` with ``revoked_at IS NULL`` to invoke a
worker.  Soft-deletes only — the audit trail outlives the grant.
"""

from __future__ import annotations

import logging
from typing import Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.models.models import WorkerTenantAccess

logger = logging.getLogger(__name__)


def is_default_allow() -> bool:
    """Read the policy flag.  Pulled into a function so tests can
    monkeypatch ``settings`` easily without import-time caching."""
    return bool(getattr(settings, "WORKER_ACCESS_DEFAULT_ALLOW", True))


async def has_access(
    db: AsyncSession,
    *,
    worker_id: str,
    tenant_id: Optional[str],
) -> bool:
    """True if *tenant_id* may invoke *worker_id*."""
    if is_default_allow():
        return True
    if not tenant_id:
        # Personal-mode (or unauth) callers have no tenant_id; in
        # default-deny mode that means no access.
        return False
    stmt = (
        select(WorkerTenantAccess.id)
        .where(
            WorkerTenantAccess.worker_id == worker_id,
            WorkerTenantAccess.tenant_id == tenant_id,
            WorkerTenantAccess.revoked_at.is_(None),
        )
        .limit(1)
    )
    try:
        return (await db.execute(stmt)).scalar_one_or_none() is not None
    except Exception as exc:  # noqa: BLE001 — never crash callers
        logger.warning("worker access check failed: %s", exc)
        return False


async def allowed_worker_ids(
    db: AsyncSession,
    *,
    tenant_id: Optional[str],
    candidate_ids: list[str],
) -> set[str]:
    """Filter *candidate_ids* to those *tenant_id* may invoke.

    Used by ``synth_worker_tools`` to skip the per-worker DB round-trip
    in the common case (default-allow → return the full set
    immediately).
    """
    if is_default_allow():
        return set(candidate_ids)
    if not tenant_id or not candidate_ids:
        return set()
    stmt = select(WorkerTenantAccess.worker_id).where(
        WorkerTenantAccess.tenant_id == tenant_id,
        WorkerTenantAccess.worker_id.in_(candidate_ids),
        WorkerTenantAccess.revoked_at.is_(None),
    )
    try:
        rows = (await db.execute(stmt)).scalars().all()
        return {r for r in rows}
    except Exception as exc:  # noqa: BLE001
        logger.warning("worker access bulk check failed: %s", exc)
        return set()
