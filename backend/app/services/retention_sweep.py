"""
GDPR Sprint 2 — Retention sweep.

Hard-deletes rows whose ``deleted_at`` is older than the configured retention
window. Runs every 6 hours, mirroring the cadence of the existing session-
memory and KB expiry sweeps in ``app.main``.

When ``RETENTION_DAYS_CONVERSATIONS`` (or *_DOCUMENTS) is 0 the corresponding
sweep is a no-op — soft-deleted rows accumulate forever until an admin
enables the policy.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

from sqlalchemy import delete, select

from app.core.config import settings

logger = logging.getLogger(__name__)


async def sweep_once() -> dict:
    """Run one retention sweep across all soft-deletable models.

    Returns a dict of {model_name: hard_deleted_count} for telemetry/audit.
    """
    from app.core.database import async_session_maker
    from app.models.models import Conversation, Message, Document, Project

    now = datetime.now(timezone.utc)
    stats: dict[str, int] = {}

    conv_days = int(getattr(settings, "RETENTION_DAYS_CONVERSATIONS", 0) or 0)
    doc_days = int(getattr(settings, "RETENTION_DAYS_DOCUMENTS", 0) or 0)

    if conv_days <= 0 and doc_days <= 0:
        return stats

    async with async_session_maker() as db:
        if conv_days > 0:
            cutoff = now - timedelta(days=conv_days)
            # Messages first (FK to conversations).
            res = await db.execute(
                delete(Message).where(
                    Message.deleted_at.is_not(None),
                    Message.deleted_at < cutoff,
                )
            )
            stats["messages"] = int(res.rowcount or 0)
            res = await db.execute(
                delete(Conversation).where(
                    Conversation.deleted_at.is_not(None),
                    Conversation.deleted_at < cutoff,
                )
            )
            stats["conversations"] = int(res.rowcount or 0)
            res = await db.execute(
                delete(Project).where(
                    Project.deleted_at.is_not(None),
                    Project.deleted_at < cutoff,
                )
            )
            stats["projects"] = int(res.rowcount or 0)

        if doc_days > 0:
            cutoff = now - timedelta(days=doc_days)
            res = await db.execute(
                delete(Document).where(
                    Document.deleted_at.is_not(None),
                    Document.deleted_at < cutoff,
                )
            )
            stats["documents"] = int(res.rowcount or 0)

        await db.commit()

    total = sum(stats.values())
    if total:
        logger.info("Retention sweep removed %d row(s): %s", total, stats)
    return stats
