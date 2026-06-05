"""
Mela AI - Collaboration Service

Handles project/chat membership, invites, audit logging for collaboration events.
"""

import logging
import uuid
from datetime import datetime, timedelta
from typing import Optional, List

from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from fastapi import HTTPException, status

from app.models.models import (
    ProjectMember, ChatMember, Invite, AuditLog, User,
    MemberRole, InviteStatus,
)

logger = logging.getLogger(__name__)


# ─────────────────────────────────────────────────────────────────────────────
# Audit logging
# ─────────────────────────────────────────────────────────────────────────────

async def audit(
    db: AsyncSession,
    actor_user_id: str,
    event_type: str,
    resource_type: str,
    resource_id: Optional[str],
    details: Optional[dict] = None,
    workspace_id: Optional[str] = None,
    success: bool = True,
) -> None:
    """Write a collaboration audit log entry."""
    try:
        entry = AuditLog(
            id=str(uuid.uuid4()),
            user_id=actor_user_id,
            action=event_type,
            event_type=event_type,
            resource_type=resource_type,
            resource_id=resource_id,
            workspace_id=workspace_id,
            details=details or {},
            success=success,
        )
        db.add(entry)
        # Flush so the record is in the transaction; caller's commit persists it.
        await db.flush()
    except Exception as exc:
        logger.warning("Audit log write failed (non-fatal): %s", exc)


# ─────────────────────────────────────────────────────────────────────────────
# Helper: resolve email → user
# ─────────────────────────────────────────────────────────────────────────────

async def resolve_user_by_email(email: str, db: AsyncSession) -> Optional[User]:
    result = await db.execute(select(User).where(User.email == email))
    return result.scalar_one_or_none()


# ─────────────────────────────────────────────────────────────────────────────
# Project membership
# ─────────────────────────────────────────────────────────────────────────────

async def list_project_members(project_id: str, db: AsyncSession) -> List[dict]:
    result = await db.execute(
        select(ProjectMember, User)
        .join(User, ProjectMember.user_id == User.id)
        .where(ProjectMember.project_id == project_id)
    )
    rows = result.all()
    return [
        {
            "id": m.id,
            "user_id": m.user_id,
            "user_email": u.email,
            "user_name": u.name,
            "role": m.role.value,
            "added_by": m.added_by,
            "added_at": m.added_at,
        }
        for m, u in rows
    ]


async def add_project_member(
    project_id: str,
    invitee_email: str,
    role: MemberRole,
    actor_id: str,
    db: AsyncSession,
    workspace_id: Optional[str] = None,
) -> dict:
    """Add or update a member on a project. Creates an Invite record and resolves immediately if user exists."""
    user = await resolve_user_by_email(invitee_email, db)

    # Create invite record
    invite = Invite(
        id=str(uuid.uuid4()),
        resource_type="project",
        resource_id=project_id,
        inviter_user_id=actor_id,
        invitee_email=invitee_email,
        invitee_user_id=user.id if user else None,
        role=role,
        status=InviteStatus.ACCEPTED if user else InviteStatus.PENDING,
        expires_at=datetime.utcnow() + timedelta(days=7),
    )
    db.add(invite)

    if user:
        # Check for existing membership
        existing = await db.execute(
            select(ProjectMember).where(
                ProjectMember.project_id == project_id,
                ProjectMember.user_id == user.id,
            )
        )
        existing_member = existing.scalar_one_or_none()
        if existing_member:
            existing_member.role = role
            member = existing_member
        else:
            member = ProjectMember(
                id=str(uuid.uuid4()),
                project_id=project_id,
                user_id=user.id,
                role=role,
                added_by=actor_id,
            )
            db.add(member)
        await db.flush()
        await audit(
            db, actor_id, "member_added", "project", project_id,
            {"invitee_email": invitee_email, "role": role.value},
            workspace_id=workspace_id,
        )
        result = await db.execute(select(User).where(User.id == user.id))
        u = result.scalar_one()
        return {
            "id": member.id,
            "user_id": user.id,
            "user_email": u.email,
            "user_name": u.name,
            "role": role.value,
            "added_by": actor_id,
            "added_at": member.added_at,
        }
    else:
        await db.flush()
        await audit(
            db, actor_id, "invite_created", "project", project_id,
            {"invitee_email": invitee_email, "role": role.value, "status": "pending_user_registration"},
            workspace_id=workspace_id,
        )
        return {
            "id": invite.id,
            "user_id": None,
            "user_email": invitee_email,
            "user_name": invitee_email,
            "role": role.value,
            "added_by": actor_id,
            "added_at": invite.created_at,
            "pending": True,
        }


async def update_project_member_role(
    project_id: str,
    target_user_id: str,
    role: MemberRole,
    actor_id: str,
    db: AsyncSession,
    workspace_id: Optional[str] = None,
) -> dict:
    result = await db.execute(
        select(ProjectMember).where(
            ProjectMember.project_id == project_id,
            ProjectMember.user_id == target_user_id,
        )
    )
    member = result.scalar_one_or_none()
    if not member:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Member not found")

    old_role = member.role.value
    member.role = role
    await db.flush()
    await audit(
        db, actor_id, "member_role_changed", "project", project_id,
        {"target_user_id": target_user_id, "old_role": old_role, "new_role": role.value},
        workspace_id=workspace_id,
    )
    user_result = await db.execute(select(User).where(User.id == target_user_id))
    u = user_result.scalar_one_or_none()
    return {
        "id": member.id,
        "user_id": target_user_id,
        "user_email": u.email if u else "",
        "user_name": u.name if u else "",
        "role": role.value,
        "added_by": member.added_by,
        "added_at": member.added_at,
    }


async def remove_project_member(
    project_id: str,
    target_user_id: str,
    actor_id: str,
    db: AsyncSession,
    workspace_id: Optional[str] = None,
) -> None:
    result = await db.execute(
        select(ProjectMember).where(
            ProjectMember.project_id == project_id,
            ProjectMember.user_id == target_user_id,
        )
    )
    member = result.scalar_one_or_none()
    if not member:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Member not found")

    await db.delete(member)
    await db.flush()
    await audit(
        db, actor_id, "member_removed", "project", project_id,
        {"removed_user_id": target_user_id},
        workspace_id=workspace_id,
    )


# ─────────────────────────────────────────────────────────────────────────────
# Chat membership
# ─────────────────────────────────────────────────────────────────────────────

async def list_chat_members(conversation_id: str, db: AsyncSession) -> List[dict]:
    result = await db.execute(
        select(ChatMember, User)
        .join(User, ChatMember.user_id == User.id)
        .where(ChatMember.conversation_id == conversation_id)
    )
    rows = result.all()
    return [
        {
            "id": m.id,
            "user_id": m.user_id,
            "user_email": u.email,
            "user_name": u.name,
            "role": m.role.value,
            "added_by": m.added_by,
            "added_at": m.added_at,
        }
        for m, u in rows
    ]


async def add_chat_member(
    conversation_id: str,
    invitee_email: str,
    role: MemberRole,
    actor_id: str,
    db: AsyncSession,
    workspace_id: Optional[str] = None,
) -> dict:
    user = await resolve_user_by_email(invitee_email, db)

    invite = Invite(
        id=str(uuid.uuid4()),
        resource_type="chat",
        resource_id=conversation_id,
        inviter_user_id=actor_id,
        invitee_email=invitee_email,
        invitee_user_id=user.id if user else None,
        role=role,
        status=InviteStatus.ACCEPTED if user else InviteStatus.PENDING,
        expires_at=datetime.utcnow() + timedelta(days=7),
    )
    db.add(invite)

    if user:
        existing = await db.execute(
            select(ChatMember).where(
                ChatMember.conversation_id == conversation_id,
                ChatMember.user_id == user.id,
            )
        )
        existing_member = existing.scalar_one_or_none()
        if existing_member:
            existing_member.role = role
            member = existing_member
        else:
            member = ChatMember(
                id=str(uuid.uuid4()),
                conversation_id=conversation_id,
                user_id=user.id,
                role=role,
                added_by=actor_id,
            )
            db.add(member)
        await db.flush()
        await audit(
            db, actor_id, "member_added", "chat", conversation_id,
            {"invitee_email": invitee_email, "role": role.value},
            workspace_id=workspace_id,
        )
        result = await db.execute(select(User).where(User.id == user.id))
        u = result.scalar_one()
        return {
            "id": member.id,
            "user_id": user.id,
            "user_email": u.email,
            "user_name": u.name,
            "role": role.value,
            "added_by": actor_id,
            "added_at": member.added_at,
        }
    else:
        await db.flush()
        await audit(
            db, actor_id, "invite_created", "chat", conversation_id,
            {"invitee_email": invitee_email, "role": role.value, "status": "pending_user_registration"},
            workspace_id=workspace_id,
        )
        return {
            "id": invite.id,
            "user_id": None,
            "user_email": invitee_email,
            "user_name": invitee_email,
            "role": role.value,
            "added_by": actor_id,
            "added_at": invite.created_at,
            "pending": True,
        }


async def update_chat_member_role(
    conversation_id: str,
    target_user_id: str,
    role: MemberRole,
    actor_id: str,
    db: AsyncSession,
    workspace_id: Optional[str] = None,
) -> dict:
    result = await db.execute(
        select(ChatMember).where(
            ChatMember.conversation_id == conversation_id,
            ChatMember.user_id == target_user_id,
        )
    )
    member = result.scalar_one_or_none()
    if not member:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Member not found")

    old_role = member.role.value
    member.role = role
    await db.flush()
    await audit(
        db, actor_id, "member_role_changed", "chat", conversation_id,
        {"target_user_id": target_user_id, "old_role": old_role, "new_role": role.value},
        workspace_id=workspace_id,
    )
    user_result = await db.execute(select(User).where(User.id == target_user_id))
    u = user_result.scalar_one_or_none()
    return {
        "id": member.id,
        "user_id": target_user_id,
        "user_email": u.email if u else "",
        "user_name": u.name if u else "",
        "role": role.value,
        "added_by": member.added_by,
        "added_at": member.added_at,
    }


async def remove_chat_member(
    conversation_id: str,
    target_user_id: str,
    actor_id: str,
    db: AsyncSession,
    workspace_id: Optional[str] = None,
) -> None:
    result = await db.execute(
        select(ChatMember).where(
            ChatMember.conversation_id == conversation_id,
            ChatMember.user_id == target_user_id,
        )
    )
    member = result.scalar_one_or_none()
    if not member:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Member not found")

    await db.delete(member)
    await db.flush()
    await audit(
        db, actor_id, "member_removed", "chat", conversation_id,
        {"removed_user_id": target_user_id},
        workspace_id=workspace_id,
    )
