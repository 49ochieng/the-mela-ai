"""
Mela AI - Resource Authorization

Single enforcement point for all collaboration and context-boundary checks.

Rules (non-negotiable):
- Private chats NEVER participate in sharing or collaboration.
- No cross-context (org vs personal) resource access.
- Resource membership role determines permitted actions.
"""

import logging
from typing import Optional
from fastapi import HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select

logger = logging.getLogger(__name__)

# Actions that are considered "collaboration" actions — all blocked on private chats.
COLLAB_ACTIONS = {
    "manage_members",
    "add_member",
    "remove_member",
    "update_member_role",
    "create_invite",
    "create_share_link",
    "view_members",
}

# Minimum role required per action (for resource-level enforcement).
# read < write < manage_members
ACTION_ROLE_REQUIREMENTS = {
    "read": {"owner", "editor", "viewer"},
    "write": {"owner", "editor"},
    "upload": {"owner", "editor"},
    "update_instructions": {"owner", "editor"},
    "manage_members": {"owner"},
    "add_member": {"owner"},
    "remove_member": {"owner"},
    "update_member_role": {"owner"},
    "view_members": {"owner", "editor", "viewer"},
    "delete": {"owner"},
    "create_invite": {"owner"},
    "create_share_link": {"owner"},
}


async def _get_project(project_id: str, db: AsyncSession):
    from app.models.models import Project
    result = await db.execute(select(Project).where(Project.id == project_id))
    return result.scalar_one_or_none()


async def _get_conversation(conversation_id: str, db: AsyncSession):
    from app.models.models import Conversation
    result = await db.execute(select(Conversation).where(Conversation.id == conversation_id))
    return result.scalar_one_or_none()


async def _get_project_member_role(project_id: str, user_id: str, db: AsyncSession) -> Optional[str]:
    from app.models.models import ProjectMember
    result = await db.execute(
        select(ProjectMember).where(
            ProjectMember.project_id == project_id,
            ProjectMember.user_id == user_id,
        )
    )
    member = result.scalar_one_or_none()
    return member.role.value if member else None


async def _get_chat_member_role(conversation_id: str, user_id: str, db: AsyncSession) -> Optional[str]:
    from app.models.models import ChatMember
    result = await db.execute(
        select(ChatMember).where(
            ChatMember.conversation_id == conversation_id,
            ChatMember.user_id == user_id,
        )
    )
    member = result.scalar_one_or_none()
    return member.role.value if member else None


def _check_role_sufficient(effective_role: str, action: str) -> bool:
    required = ACTION_ROLE_REQUIREMENTS.get(action, {"owner"})
    return effective_role in required


def _enforce_profile_tenant(
    *,
    record,
    resource_kind: str,
    resource_id: str,
    user_id: str,
    expected_profile_mode: str | None,
    expected_tenant_id: str | None,
) -> None:
    """Fail-closed boundary check: reject if record's profile/tenant does not match expected.

    A `None` expected value means the caller did not bind the request to a profile/tenant
    context and is intentionally relaxing that boundary (e.g. background jobs). Endpoints
    serving authenticated users MUST pass both values from the request profile context.
    """
    if expected_profile_mode is not None:
        actual_mode = getattr(record, "profile_mode", None) or getattr(record, "context_type", None)
        if actual_mode is not None and actual_mode != expected_profile_mode:
            logger.warning(
                "Profile mismatch on %s %s by user %s: expected=%s actual=%s",
                resource_kind, resource_id, user_id, expected_profile_mode, actual_mode,
            )
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Resource is not accessible from the current profile mode",
            )
    if expected_tenant_id is not None:
        actual_tenant = getattr(record, "tenant_id", None)
        # Allow tenant-less personal records when caller is in personal mode; otherwise enforce.
        if actual_tenant is not None and actual_tenant != expected_tenant_id:
            logger.warning(
                "Tenant mismatch on %s %s by user %s: expected=%s actual=%s",
                resource_kind, resource_id, user_id, expected_tenant_id, actual_tenant,
            )
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Resource belongs to a different tenant",
            )


async def check_project_access(
    project_id: str,
    user_id: str,
    action: str,
    db: AsyncSession,
    expected_profile_mode: str | None = None,
    expected_tenant_id: str | None = None,
) -> str:
    """
    Verify that `user_id` may perform `action` on a project.
    Returns the effective role string on success.
    Raises HTTP 403 or 404 on failure.
    """
    project = await _get_project(project_id, db)
    if not project:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Project not found")

    # Profile/tenant boundary enforcement — fail-closed cross-context access.
    _enforce_profile_tenant(
        record=project,
        resource_kind="project",
        resource_id=project_id,
        user_id=user_id,
        expected_profile_mode=expected_profile_mode,
        expected_tenant_id=expected_tenant_id,
    )

    # Personal projects cannot be shared or have collaborators.
    if getattr(project, "context_type", "personal") == "personal" and action in COLLAB_ACTIONS:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Personal projects cannot be shared. Switch to Work mode to use collaboration features.",
        )

    # Determine effective role: owner if the project was created by this user
    if project.user_id == user_id:
        effective_role = "owner"
    else:
        member_role = await _get_project_member_role(project_id, user_id, db)
        if member_role is None:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="You do not have access to this project",
            )
        effective_role = member_role

    if not _check_role_sufficient(effective_role, action):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=f"Your role '{effective_role}' is not permitted to perform '{action}' on this project",
        )

    return effective_role


async def check_chat_access(
    conversation_id: str,
    user_id: str,
    action: str,
    db: AsyncSession,
    expected_profile_mode: str | None = None,
    expected_tenant_id: str | None = None,
) -> str:
    """
    Verify that `user_id` may perform `action` on a conversation.

    Non-negotiable: private chats block ALL collaboration actions.
    Returns the effective role string on success.
    Raises HTTP 403 or 404 on failure.
    """
    conversation = await _get_conversation(conversation_id, db)
    if not conversation:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Conversation not found")

    # Profile/tenant boundary enforcement — fail-closed cross-context access.
    _enforce_profile_tenant(
        record=conversation,
        resource_kind="conversation",
        resource_id=conversation_id,
        user_id=user_id,
        expected_profile_mode=expected_profile_mode,
        expected_tenant_id=expected_tenant_id,
    )

    # Hard block: private chats cannot be shared under any circumstance.
    if conversation.is_private and action in COLLAB_ACTIONS:
        logger.warning(
            "Blocked collaboration action '%s' on private chat %s by user %s",
            action, conversation_id, user_id,
        )
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Private chats cannot be shared or have collaborators",
        )

    # Personal chats cannot be shared — collaboration is Work mode only.
    if getattr(conversation, "context_type", "personal") == "personal" and action in COLLAB_ACTIONS:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Personal chats cannot be shared. Switch to Work mode to use collaboration features.",
        )

    # Determine effective role
    if conversation.user_id == user_id:
        effective_role = "owner"
    else:
        member_role = await _get_chat_member_role(conversation_id, user_id, db)
        if member_role is None:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="You do not have access to this conversation",
            )
        effective_role = member_role

    if not _check_role_sufficient(effective_role, action):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=f"Your role '{effective_role}' is not permitted to perform '{action}' on this conversation",
        )

    return effective_role


async def assert_not_private_for_project_move(conversation_id: str, db: AsyncSession):
    """
    Verify a conversation is not private before allowing it to be moved into a project.
    Raises HTTP 403 if the conversation is private.
    """
    conversation = await _get_conversation(conversation_id, db)
    if not conversation:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Conversation not found")
    if conversation.is_private:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Private chats cannot be moved into shared projects",
        )
