"""Account deletion worker — completes the GDPR right-to-erasure flow.

``POST /api/me/delete`` (see ``app.routers.privacy``) revokes the user's
sessions/tokens and stamps ``users.deletion_requested_at``. This worker
runs daily and hard-deletes every per-user row for any user whose stamp
is older than ``GRACE_PERIOD_DAYS``.

Design notes:
    - Soft-then-hard separation gives users a grace window to cancel and
      prevents an attacker with a stolen session from instantly nuking
      the account.
    - Tenant rows are NEVER deleted by this worker; tenants are deleted
      only when their last user is deleted (and even then, conservatively
      kept around — multi-tenant SaaS rarely wants to delete the tenant
      shell).
    - The audit trail is preserved: ``audit_logs`` rows are NOT deleted
      because they contain the tamper-evident hash chain. We instead
      anonymise them by clearing ``user_id``-bearing detail fields.
    - All deletes happen in one transaction per user. If anything raises,
      the user remains marked and we retry tomorrow.

Run as a one-shot:
    python -m app.workers.account_deleter
The scheduler process also schedules ``run_due_deletions()`` daily at
03:00 UTC (see ``app.scheduler.scheduler``).
"""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..database import session_scope
from ..logging_config import setup_logging
from ..models import (
    AgentToken,
    AuditLog,
    GraphConnection,
    OAuthState,
    ScanEvent,
    ScanRun,
    ScanSettings,
    Session as SessionModel,
    SourceMessage,
    Task,
    TaskAttachment,
    TaskSync,
    User,
)
from ..services.tasks.audit import log as audit_log

logger = logging.getLogger("app.account_deleter")

# Default grace window. Matches the value reported by /api/me/delete so
# the user-facing promise and the worker behaviour stay in lockstep.
GRACE_PERIOD_DAYS = 30

# Per-user tables to hard-delete (order: leaves first, roots last).
# Audit_logs is intentionally absent — see module docstring.
_PER_USER_TABLES = (
    TaskAttachment,
    TaskSync,
    Task,
    SourceMessage,
    ScanEvent,
    ScanRun,
    ScanSettings,
    GraphConnection,
    AgentToken,
    SessionModel,
    OAuthState,
)


async def _hard_delete_user(session: AsyncSession, user: User) -> dict[str, int]:
    """Delete all per-user rows for ``user``. Returns rowcounts per table."""
    counts: dict[str, int] = {}
    for model in _PER_USER_TABLES:
        # OAuthState is keyed by user_id but may be nullable; same with
        # AuditLog (handled separately). Use generic where clause.
        where_user_id = getattr(model, "user_id", None)
        where_tenant_id = getattr(model, "tenant_id", None)
        if where_user_id is None:
            continue
        stmt = delete(model).where(where_user_id == user.id)
        if where_tenant_id is not None:
            stmt = stmt.where(where_tenant_id == user.tenant_id)
        res = await session.execute(stmt)
        counts[model.__tablename__] = res.rowcount or 0

    # Anonymise audit log entries — preserve the chain but strip identity.
    audit_stmt = (
        select(AuditLog)
        .where(AuditLog.user_id == user.id, AuditLog.tenant_id == user.tenant_id)
    )
    audit_rows = (await session.execute(audit_stmt)).scalars().all()
    for row in audit_rows:
        row.user_id = None
        row.ip = None
        row.user_agent = None
        # details_json may contain identifiers; strip in place.
        if isinstance(row.details_json, dict):
            scrubbed = {
                k: ("[redacted]" if isinstance(v, str) and user.id in v else v)
                for k, v in row.details_json.items()
            }
            row.details_json = scrubbed
    counts["audit_logs_anonymised"] = len(audit_rows)

    # Finally, delete the user row itself.
    await session.delete(user)
    counts["users"] = 1
    return counts


async def _process_due(session: AsyncSession, *, grace_days: int, now: datetime) -> int:
    cutoff = now - timedelta(days=grace_days)
    due = (
        await session.execute(
            select(User).where(
                User.deletion_requested_at.is_not(None),
                User.deletion_requested_at <= cutoff,
            )
        )
    ).scalars().all()
    if not due:
        return 0
    processed = 0
    for user in due:
        try:
            counts = await _hard_delete_user(session, user)
            # Audit BEFORE commit so the entry is part of the same tx;
            # use tenant context from the doomed user.
            await audit_log(
                session,
                tenant_id=user.tenant_id,
                user_id=None,  # user no longer exists by end of tx
                action="me.delete_executed",
                entity_type="user",
                entity_id=user.id,
                details=counts,
            )
            await session.commit()
            processed += 1
            logger.info(
                "account_deleter: hard-deleted user=%s tenant=%s counts=%s",
                user.id, user.tenant_id, counts,
            )
        except Exception:
            await session.rollback()
            logger.exception(
                "account_deleter: failed for user=%s tenant=%s",
                user.id, user.tenant_id,
            )
    return processed


async def run_due_deletions(*, grace_days: int = GRACE_PERIOD_DAYS,
                            now: datetime | None = None) -> int:
    """Entry point used by the scheduler and the CLI."""
    n = now or datetime.utcnow()
    async with session_scope() as session:
        return await _process_due(session, grace_days=grace_days, now=n)


async def main() -> None:  # pragma: no cover - CLI shim
    setup_logging()
    processed = await run_due_deletions()
    logger.info("account_deleter: processed %d user(s)", processed)


if __name__ == "__main__":  # pragma: no cover
    asyncio.run(main())
