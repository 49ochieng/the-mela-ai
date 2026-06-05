"""
Mela AI - Collaboration Endpoints

Membership management for projects and standard chats.
Private chats are rejected server-side with HTTP 403.
"""

import logging
from fastapi import APIRouter, Depends, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.core.profile_context import ProfileContext, get_profile_context
from app.core.security import get_current_user
from app.core.authorization import check_project_access, check_chat_access
from app.schemas.auth import UserInfo
from app.schemas.collaboration import (
    AddMemberRequest, UpdateMemberRoleRequest,
)
import app.services.collaboration_service as collab_svc

logger = logging.getLogger(__name__)
router = APIRouter()


# ─────────────────────────────────────────────────────────────────────────────
# Project membership endpoints
# ─────────────────────────────────────────────────────────────────────────────

@router.get("/projects/{project_id}/members", response_model=list[dict])
async def get_project_members(
    project_id: str,
    current_user: UserInfo = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
    profile_ctx: ProfileContext = Depends(get_profile_context),
):
    """List all members of a project. Requires at least viewer role."""
    await check_project_access(
        project_id,
        current_user.id,
        "view_members",
        db,
        expected_profile_mode=profile_ctx.profile_mode,
        expected_tenant_id=profile_ctx.db_tenant_id,
    )
    return await collab_svc.list_project_members(project_id, db)


@router.post("/projects/{project_id}/members", status_code=status.HTTP_201_CREATED)
async def add_project_member(
    project_id: str,
    body: AddMemberRequest,
    current_user: UserInfo = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
    profile_ctx: ProfileContext = Depends(get_profile_context),
):
    """Invite a user (by email) to a project. Requires owner role."""
    await check_project_access(
        project_id,
        current_user.id,
        "add_member",
        db,
        expected_profile_mode=profile_ctx.profile_mode,
        expected_tenant_id=profile_ctx.db_tenant_id,
    )
    return await collab_svc.add_project_member(
        project_id=project_id,
        invitee_email=body.email,
        role=body.role,
        actor_id=current_user.id,
        db=db,
    )


@router.patch("/projects/{project_id}/members/{user_id}")
async def update_project_member_role(
    project_id: str,
    user_id: str,
    body: UpdateMemberRoleRequest,
    current_user: UserInfo = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
    profile_ctx: ProfileContext = Depends(get_profile_context),
):
    """Change a member's role. Requires owner role."""
    await check_project_access(
        project_id,
        current_user.id,
        "update_member_role",
        db,
        expected_profile_mode=profile_ctx.profile_mode,
        expected_tenant_id=profile_ctx.db_tenant_id,
    )
    return await collab_svc.update_project_member_role(
        project_id=project_id,
        target_user_id=user_id,
        role=body.role,
        actor_id=current_user.id,
        db=db,
    )


@router.delete("/projects/{project_id}/members/{user_id}", status_code=status.HTTP_204_NO_CONTENT)
async def remove_project_member(
    project_id: str,
    user_id: str,
    current_user: UserInfo = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
    profile_ctx: ProfileContext = Depends(get_profile_context),
):
    """Remove a member from a project. Requires owner role."""
    await check_project_access(
        project_id,
        current_user.id,
        "remove_member",
        db,
        expected_profile_mode=profile_ctx.profile_mode,
        expected_tenant_id=profile_ctx.db_tenant_id,
    )
    await collab_svc.remove_project_member(
        project_id=project_id,
        target_user_id=user_id,
        actor_id=current_user.id,
        db=db,
    )


# ─────────────────────────────────────────────────────────────────────────────
# Chat membership endpoints (standard chats only — private chats return 403)
# ─────────────────────────────────────────────────────────────────────────────

@router.get("/chats/{chat_id}/members", response_model=list[dict])
async def get_chat_members(
    chat_id: str,
    current_user: UserInfo = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
    profile_ctx: ProfileContext = Depends(get_profile_context),
):
    """
    List all members of a shared conversation.
    Returns 403 if the conversation is private.
    """
    await check_chat_access(
        chat_id,
        current_user.id,
        "view_members",
        db,
        expected_profile_mode=profile_ctx.profile_mode,
        expected_tenant_id=profile_ctx.db_tenant_id,
    )
    return await collab_svc.list_chat_members(chat_id, db)


@router.post("/chats/{chat_id}/members", status_code=status.HTTP_201_CREATED)
async def add_chat_member(
    chat_id: str,
    body: AddMemberRequest,
    current_user: UserInfo = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
    profile_ctx: ProfileContext = Depends(get_profile_context),
):
    """
    Invite a user to a shared conversation.
    Returns 403 if the conversation is private.
    """
    await check_chat_access(
        chat_id,
        current_user.id,
        "add_member",
        db,
        expected_profile_mode=profile_ctx.profile_mode,
        expected_tenant_id=profile_ctx.db_tenant_id,
    )
    return await collab_svc.add_chat_member(
        conversation_id=chat_id,
        invitee_email=body.email,
        role=body.role,
        actor_id=current_user.id,
        db=db,
    )


@router.patch("/chats/{chat_id}/members/{user_id}")
async def update_chat_member_role(
    chat_id: str,
    user_id: str,
    body: UpdateMemberRoleRequest,
    current_user: UserInfo = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
    profile_ctx: ProfileContext = Depends(get_profile_context),
):
    """
    Update a member's role in a shared conversation.
    Returns 403 if the conversation is private.
    """
    await check_chat_access(
        chat_id,
        current_user.id,
        "update_member_role",
        db,
        expected_profile_mode=profile_ctx.profile_mode,
        expected_tenant_id=profile_ctx.db_tenant_id,
    )
    return await collab_svc.update_chat_member_role(
        conversation_id=chat_id,
        target_user_id=user_id,
        role=body.role,
        actor_id=current_user.id,
        db=db,
    )


@router.delete("/chats/{chat_id}/members/{user_id}", status_code=status.HTTP_204_NO_CONTENT)
async def remove_chat_member(
    chat_id: str,
    user_id: str,
    current_user: UserInfo = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
    profile_ctx: ProfileContext = Depends(get_profile_context),
):
    """
    Remove a member from a shared conversation.
    Returns 403 if the conversation is private.
    """
    await check_chat_access(
        chat_id,
        current_user.id,
        "remove_member",
        db,
        expected_profile_mode=profile_ctx.profile_mode,
        expected_tenant_id=profile_ctx.db_tenant_id,
    )
    await collab_svc.remove_chat_member(
        conversation_id=chat_id,
        target_user_id=user_id,
        actor_id=current_user.id,
        db=db,
    )
