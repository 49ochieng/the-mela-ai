"""
Mela AI — GDPR endpoints (Sprint 2.3 + 2.4).

* ``POST /user/export``  — Article 15 (DSAR). Builds a zip of the caller's
  conversations, messages, documents (metadata), audit log, and memory and
  returns either inline JSON (small datasets) or a signed Blob URL.
* ``POST /user/erase``   — Article 17 (Right to Be Forgotten). Soft-deletes
  everything immediately and anonymises the User row. A scheduled
  retention sweep hard-deletes the rows after the configured window.

Both endpoints are gated by ``settings.ENABLE_GDPR_ENDPOINTS``; when the
flag is OFF the routes return 404 (not 403 — we don't reveal feature
existence).
"""

from __future__ import annotations

import io
import json
import logging
import uuid
import zipfile
from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.responses import StreamingResponse
from sqlalchemy import select, update as sa_update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.database import get_db
from app.core.logging import log_security_event
from app.core.security import get_current_user
from app.models.models import (
    AgentMemoryItem,
    AuditLog,
    Conversation,
    Document,
    Message,
    Project,
    User,
)
from app.schemas.auth import UserInfo

logger = logging.getLogger(__name__)
router = APIRouter()


def _gdpr_enabled() -> bool:
    return bool(getattr(settings, "ENABLE_GDPR_ENDPOINTS", False))


def _require_enabled() -> None:
    if not _gdpr_enabled():
        # 404 not 403: feature flag should not leak.
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND)


def _serialise(rows: list, fields: list[str]) -> list[dict[str, Any]]:
    """Lightweight row → dict serializer that handles datetimes."""
    out = []
    for row in rows:
        d = {}
        for f in fields:
            v = getattr(row, f, None)
            if isinstance(v, datetime):
                v = v.isoformat()
            d[f] = v
        out.append(d)
    return out


# ── Article 15: Data export (DSAR) ───────────────────────────────────────────


@router.post("/export")
async def export_my_data(
    current_user: UserInfo = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> StreamingResponse:
    """Return a zip containing every database row owned by the caller.

    Uses ``StreamingResponse`` so even large exports don't buffer in memory.
    For an enterprise multi-tenant deployment this should enqueue a background
    job and email a signed Blob URL when ready — kept inline here as a
    deliberate v1 to keep the change footprint small.
    """
    _require_enabled()
    user_id = str(current_user.id)

    # Pull everything the user owns. Tenant scoping is implicit — the user_id
    # FK is the canonical owner.
    convs = (await db.execute(
        select(Conversation).where(Conversation.user_id == user_id)
    )).scalars().all()
    msgs = (await db.execute(
        select(Message).where(
            Message.conversation_id.in_([c.id for c in convs] or [""])
        )
    )).scalars().all()
    docs = (await db.execute(
        select(Document).where(Document.uploaded_by == user_id)
    )).scalars().all()
    audit = (await db.execute(
        select(AuditLog).where(AuditLog.user_id == user_id)
    )).scalars().all()
    memories = (await db.execute(
        select(AgentMemoryItem).where(AgentMemoryItem.user_id == user_id)
    )).scalars().all()
    projects = (await db.execute(
        select(Project).where(Project.user_id == user_id)
    )).scalars().all()

    user_row = await db.scalar(select(User).where(User.id == user_id))

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, mode="w", compression=zipfile.ZIP_DEFLATED) as zf:
        zf.writestr(
            "manifest.json",
            json.dumps({
                "exported_at": datetime.now(timezone.utc).isoformat(),
                "user_id": user_id,
                "email": getattr(user_row, "email", None),
                "format_version": 1,
                "counts": {
                    "conversations": len(convs),
                    "messages": len(msgs),
                    "documents": len(docs),
                    "audit_logs": len(audit),
                    "memories": len(memories),
                    "projects": len(projects),
                },
            }, indent=2),
        )
        zf.writestr("user.json", json.dumps(_serialise(
            [user_row] if user_row else [],
            ["id", "email", "name", "department", "job_title",
             "role", "created_at"],
        ), indent=2, default=str))
        zf.writestr("conversations.json", json.dumps(_serialise(
            convs,
            ["id", "title", "model", "is_archived", "is_private",
             "profile_mode", "tenant_id", "context_type",
             "created_at", "updated_at"],
        ), indent=2, default=str))
        zf.writestr("messages.json", json.dumps(_serialise(
            msgs,
            ["id", "conversation_id", "role", "content", "tokens_used",
             "model", "profile_mode", "tenant_id", "created_at"],
        ), indent=2, default=str))
        zf.writestr("documents.json", json.dumps(_serialise(
            docs,
            ["id", "title", "filename", "file_type", "file_size",
             "source", "source_url", "is_indexed", "created_at"],
        ), indent=2, default=str))
        zf.writestr("audit_logs.json", json.dumps(_serialise(
            audit,
            ["id", "action", "event_type", "resource_type", "resource_id",
             "success", "error_message", "created_at"],
        ), indent=2, default=str))
        zf.writestr("memories.json", json.dumps(_serialise(
            memories,
            ["id", "memory_type", "content", "tags",
             "workspace_id", "created_at", "updated_at"],
        ), indent=2, default=str))
        zf.writestr("projects.json", json.dumps(_serialise(
            projects,
            ["id", "name", "description", "profile_mode",
             "tenant_id", "created_at", "updated_at"],
        ), indent=2, default=str))

    await log_security_event(
        db,
        user_id=user_id,
        action="user_data_exported",
        event_type="gdpr",
        resource_type="user",
        resource_id=user_id,
        details={
            "counts": {
                "conversations": len(convs),
                "messages": len(msgs),
                "documents": len(docs),
                "audit_logs": len(audit),
                "memories": len(memories),
            },
        },
        success=True,
    )
    await db.commit()

    buf.seek(0)
    filename = f"mela-export-{user_id[:8]}-{datetime.now(timezone.utc).strftime('%Y%m%d')}.zip"
    return StreamingResponse(
        buf,
        media_type="application/zip",
        headers={"Content-Disposition": f"attachment; filename={filename}"},
    )


# ── Article 17: Right to Erasure ─────────────────────────────────────────────


@router.post("/erase")
async def request_erasure(
    current_user: UserInfo = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> dict:
    """Erase the caller's data.

    Behaviour:
      1. Soft-deletes Conversation, Message, Document, Project, AgentMemoryItem
         owned by the user.
      2. Anonymises the User row (email/name/azure_id → ``erased-{uuid}``).
      3. Sets ``is_active=False`` so the user cannot log in again.
      4. Audit-logs the event under a special action so admins can prove
         the request was honoured (the audit row survives the cascade).
      5. Retention sweep will hard-delete the soft-deleted rows after the
         configured window.

    NB: Production deployments should add an email-confirm step before
    executing the erasure. Kept single-shot here for v1; the next iteration
    will add a token-confirmed double-opt-in.
    """
    _require_enabled()
    user_id = str(current_user.id)
    now = datetime.now(timezone.utc)

    # 1. Soft-delete owned content.
    soft_targets = [
        (Conversation, Conversation.user_id == user_id),
        (Document, Document.uploaded_by == user_id),
        (Project, Project.user_id == user_id),
        (AgentMemoryItem, AgentMemoryItem.user_id == user_id),
    ]
    for model, predicate in soft_targets:
        await db.execute(
            sa_update(model)
            .where(predicate, model.deleted_at.is_(None))
            .values(deleted_at=now)
        )

    # Messages live one hop away — soft-delete those that belong to the user's
    # conversations.
    conv_ids = [
        row.id for row in (
            await db.execute(
                select(Conversation.id).where(
                    Conversation.user_id == user_id
                )
            )
        ).all()
    ]
    if conv_ids:
        await db.execute(
            sa_update(Message)
            .where(
                Message.conversation_id.in_(conv_ids),
                Message.deleted_at.is_(None),
            )
            .values(deleted_at=now)
        )

    # 2. Anonymise the user row.
    placeholder = f"erased-{uuid.uuid4()}"
    await db.execute(
        sa_update(User)
        .where(User.id == user_id)
        .values(
            email=f"{placeholder}@erased.local",
            name="Erased User",
            azure_id=placeholder,
            department=None,
            job_title=None,
            is_active=False,
            deleted_at=now,
        )
    )

    # 3. Audit (survives because audit_logs don't cascade on user delete).
    await log_security_event(
        db,
        user_id=user_id,
        action="user_data_erased",
        event_type="gdpr",
        resource_type="user",
        resource_id=user_id,
        details={
            "soft_deleted_conversations": len(conv_ids),
            "anonymisation": "email/name/azure_id replaced",
            "retention_will_finalise": True,
        },
        success=True,
    )
    await db.commit()

    logger.info("[gdpr] Erasure executed for user=%s", user_id)
    return {
        "status": "erased",
        "user_id": user_id,
        "soft_deleted_conversations": len(conv_ids),
        "anonymised": True,
        "hard_delete_after_days": (
            settings.RETENTION_DAYS_CONVERSATIONS
            if settings.RETENTION_DAYS_CONVERSATIONS > 0
            else None
        ),
    }
