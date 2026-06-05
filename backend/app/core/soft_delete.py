"""
GDPR Sprint 2 — Soft-delete helpers.

When ``settings.ENABLE_SOFT_DELETE`` is True, list/read queries should filter
out rows where ``deleted_at IS NOT NULL``. This module centralises that
filter so individual endpoints don't have to remember the flag check.

Usage::

    from app.core.soft_delete import filter_deleted
    stmt = select(Conversation).where(Conversation.user_id == uid)
    stmt = filter_deleted(stmt, Conversation)
"""

from __future__ import annotations

from typing import Any

from app.core.config import settings


def is_soft_delete_enabled() -> bool:
    return bool(getattr(settings, "ENABLE_SOFT_DELETE", False))


def filter_deleted(stmt, model) -> Any:
    """Return ``stmt`` with a ``deleted_at IS NULL`` filter when soft-delete
    is enabled and the model has the column.

    Safe to call on every list query — when the flag is off or the model
    doesn't have the column, the statement passes through unchanged.
    """
    if not is_soft_delete_enabled():
        return stmt
    if not hasattr(model, "deleted_at"):
        return stmt
    return stmt.where(model.deleted_at.is_(None))
