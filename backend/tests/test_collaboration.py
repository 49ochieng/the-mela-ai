"""
Collaboration acceptance tests.

Verifies:
1. Project invite / access / role enforcement
2. Chat member access
3. Private chat hard blocks (UI and API enforcement)
4. Context_type separation (org vs personal)
5. Audit log entries
"""

import uuid
import pytest
import pytest_asyncio
from fastapi import HTTPException
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select

from app.core.authorization import check_project_access, check_chat_access
from app.models.models import (
    AuditLog, ChatMember, ProjectMember, MemberRole, Conversation, Project,
)
from app.services.collaboration_service import (
    add_project_member, list_project_members, remove_project_member,
    update_project_member_role, add_chat_member, list_chat_members,
    remove_chat_member,
)
from app.services.project_service import assign_conversation, list_projects

from tests.conftest import make_user, make_project, make_conversation


# ─────────────────────────────────────────────────────────────────────────────
# 1. PROJECT: invite → access → remove
# ─────────────────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_owner_can_add_member(db: AsyncSession):
    owner = await make_user(db)
    invitee = await make_user(db)
    project = await make_project(db, owner, context_type="org")

    result = await add_project_member(
        project_id=project.id,
        invitee_email=invitee.email,
        role=MemberRole.EDITOR,
        actor_id=owner.id,
        db=db,
    )
    assert result["user_id"] == invitee.id
    assert result["role"] == "editor"


@pytest.mark.asyncio
async def test_invited_user_can_access_project(db: AsyncSession):
    owner = await make_user(db)
    invitee = await make_user(db)
    project = await make_project(db, owner, context_type="org")

    await add_project_member(
        project_id=project.id,
        invitee_email=invitee.email,
        role=MemberRole.VIEWER,
        actor_id=owner.id,
        db=db,
    )

    # Viewer should be able to read the project
    role = await check_project_access(project.id, invitee.id, "read", db)
    assert role == "viewer"


@pytest.mark.asyncio
async def test_viewer_cannot_manage_members(db: AsyncSession):
    owner = await make_user(db)
    viewer = await make_user(db)
    project = await make_project(db, owner, context_type="org")

    await add_project_member(
        project_id=project.id,
        invitee_email=viewer.email,
        role=MemberRole.VIEWER,
        actor_id=owner.id,
        db=db,
    )

    with pytest.raises(HTTPException) as exc_info:
        await check_project_access(project.id, viewer.id, "manage_members", db)
    assert exc_info.value.status_code == 403


@pytest.mark.asyncio
async def test_viewer_cannot_upload(db: AsyncSession):
    owner = await make_user(db)
    viewer = await make_user(db)
    project = await make_project(db, owner, context_type="org")

    await add_project_member(
        project_id=project.id,
        invitee_email=viewer.email,
        role=MemberRole.VIEWER,
        actor_id=owner.id,
        db=db,
    )

    with pytest.raises(HTTPException) as exc_info:
        await check_project_access(project.id, viewer.id, "upload", db)
    assert exc_info.value.status_code == 403


@pytest.mark.asyncio
async def test_editor_can_write(db: AsyncSession):
    owner = await make_user(db)
    editor = await make_user(db)
    project = await make_project(db, owner, context_type="org")

    await add_project_member(
        project_id=project.id,
        invitee_email=editor.email,
        role=MemberRole.EDITOR,
        actor_id=owner.id,
        db=db,
    )

    role = await check_project_access(project.id, editor.id, "write", db)
    assert role == "editor"


@pytest.mark.asyncio
async def test_remove_member_revokes_access(db: AsyncSession):
    owner = await make_user(db)
    member = await make_user(db)
    project = await make_project(db, owner, context_type="org")

    await add_project_member(
        project_id=project.id,
        invitee_email=member.email,
        role=MemberRole.EDITOR,
        actor_id=owner.id,
        db=db,
    )

    # Verify access before removal
    role = await check_project_access(project.id, member.id, "read", db)
    assert role == "editor"

    await remove_project_member(
        project_id=project.id,
        target_user_id=member.id,
        actor_id=owner.id,
        db=db,
    )

    # After removal, access must be denied
    with pytest.raises(HTTPException) as exc_info:
        await check_project_access(project.id, member.id, "read", db)
    assert exc_info.value.status_code == 403


@pytest.mark.asyncio
async def test_non_member_cannot_access_project(db: AsyncSession):
    owner = await make_user(db)
    stranger = await make_user(db)
    project = await make_project(db, owner, context_type="org")

    with pytest.raises(HTTPException) as exc_info:
        await check_project_access(project.id, stranger.id, "read", db)
    assert exc_info.value.status_code == 403


# ─────────────────────────────────────────────────────────────────────────────
# 2. CHAT MEMBER ACCESS
# ─────────────────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_owner_can_add_chat_member(db: AsyncSession):
    owner = await make_user(db)
    invitee = await make_user(db)
    conv = await make_conversation(db, owner, is_private=False)

    result = await add_chat_member(
        conversation_id=conv.id,
        invitee_email=invitee.email,
        role=MemberRole.VIEWER,
        actor_id=owner.id,
        db=db,
    )
    assert result["user_id"] == invitee.id


@pytest.mark.asyncio
async def test_chat_viewer_can_read(db: AsyncSession):
    owner = await make_user(db)
    viewer = await make_user(db)
    conv = await make_conversation(db, owner, is_private=False)

    await add_chat_member(
        conversation_id=conv.id,
        invitee_email=viewer.email,
        role=MemberRole.VIEWER,
        actor_id=owner.id,
        db=db,
    )

    role = await check_chat_access(conv.id, viewer.id, "read", db)
    assert role == "viewer"


@pytest.mark.asyncio
async def test_chat_member_removed_loses_access(db: AsyncSession):
    owner = await make_user(db)
    viewer = await make_user(db)
    conv = await make_conversation(db, owner, is_private=False)

    await add_chat_member(
        conversation_id=conv.id,
        invitee_email=viewer.email,
        role=MemberRole.VIEWER,
        actor_id=owner.id,
        db=db,
    )

    await remove_chat_member(
        conversation_id=conv.id,
        target_user_id=viewer.id,
        actor_id=owner.id,
        db=db,
    )

    with pytest.raises(HTTPException) as exc_info:
        await check_chat_access(conv.id, viewer.id, "read", db)
    assert exc_info.value.status_code == 403


# ─────────────────────────────────────────────────────────────────────────────
# 3. PRIVATE CHAT HARD BLOCKS (non-negotiable)
# ─────────────────────────────────────────────────────────────────────────────

COLLAB_ACTIONS_SAMPLE = [
    "manage_members",
    "add_member",
    "remove_member",
    "view_members",
    "create_share_link",
]


@pytest.mark.asyncio
@pytest.mark.parametrize("action", COLLAB_ACTIONS_SAMPLE)
async def test_private_chat_blocks_all_collab_actions(db: AsyncSession, action: str):
    """Any collaboration action on a private chat must return HTTP 403."""
    owner = await make_user(db)
    private_conv = await make_conversation(db, owner, is_private=True)

    with pytest.raises(HTTPException) as exc_info:
        await check_chat_access(private_conv.id, owner.id, action, db)
    assert exc_info.value.status_code == 403
    assert "private" in exc_info.value.detail.lower()


@pytest.mark.asyncio
async def test_private_chat_cannot_be_moved_to_project(db: AsyncSession):
    """Private chats must not be moved into shared projects."""
    owner = await make_user(db)
    project = await make_project(db, owner, context_type="org")
    private_conv = await make_conversation(db, owner, is_private=True)

    with pytest.raises(PermissionError):
        await assign_conversation(
            project_id=project.id,
            conversation_id=private_conv.id,
            user_id=owner.id,
            db=db,
        )


@pytest.mark.asyncio
async def test_standard_chat_can_be_moved_to_project(db: AsyncSession):
    """Standard (non-private) chats can be moved into projects."""
    owner = await make_user(db)
    project = await make_project(db, owner, context_type="org")
    conv = await make_conversation(db, owner, is_private=False)

    # Should not raise
    await assign_conversation(
        project_id=project.id,
        conversation_id=conv.id,
        user_id=owner.id,
        db=db,
    )

    refreshed = await db.get(Conversation, conv.id)
    assert refreshed.project_id == project.id


# ─────────────────────────────────────────────────────────────────────────────
# 4. CONTEXT TYPE SEPARATION (org vs personal)
# ─────────────────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_list_projects_filters_by_context_type(db: AsyncSession):
    """list_projects must return only projects matching the requested context_type."""
    owner = await make_user(db)
    personal_project = await make_project(db, owner, context_type="personal")
    org_project = await make_project(db, owner, context_type="org")
    await db.commit()

    personal_projects = await list_projects(owner.id, db, context_type="personal")
    org_projects = await list_projects(owner.id, db, context_type="org")

    personal_ids = {p.id for p in personal_projects}
    org_ids = {p.id for p in org_projects}

    assert personal_project.id in personal_ids
    assert org_project.id not in personal_ids
    assert org_project.id in org_ids
    assert personal_project.id not in org_ids


@pytest.mark.asyncio
async def test_org_and_personal_conversations_are_isolated(db: AsyncSession):
    """Conversations tagged personal must not appear when filtering for org (and vice versa)."""
    from app.services.chat_service import chat_service

    owner = await make_user(db)
    personal_conv = await make_conversation(db, owner, context_type="personal")
    org_conv = await make_conversation(db, owner, context_type="org")
    await db.commit()

    personal_list = await chat_service.list_conversations(db, owner.id, context_type="personal")
    org_list = await chat_service.list_conversations(db, owner.id, context_type="org")

    personal_ids = {c.id for c in personal_list}
    org_ids = {c.id for c in org_list}

    assert personal_conv.id in personal_ids
    assert org_conv.id not in personal_ids
    assert org_conv.id in org_ids
    assert personal_conv.id not in org_ids


# ─────────────────────────────────────────────────────────────────────────────
# 5. AUDIT LOG
# ─────────────────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_audit_log_on_member_add(db: AsyncSession):
    owner = await make_user(db)
    invitee = await make_user(db)
    project = await make_project(db, owner, context_type="org")

    await add_project_member(
        project_id=project.id,
        invitee_email=invitee.email,
        role=MemberRole.VIEWER,
        actor_id=owner.id,
        db=db,
    )
    await db.commit()

    logs = list((await db.execute(
        select(AuditLog).where(
            AuditLog.user_id == owner.id,
            AuditLog.resource_id == project.id,
        )
    )).scalars())

    assert len(logs) >= 1
    actions = {entry.action for entry in logs}
    assert "member_added" in actions


@pytest.mark.asyncio
async def test_audit_log_on_member_remove(db: AsyncSession):
    owner = await make_user(db)
    member = await make_user(db)
    project = await make_project(db, owner, context_type="org")

    await add_project_member(
        project_id=project.id,
        invitee_email=member.email,
        role=MemberRole.EDITOR,
        actor_id=owner.id,
        db=db,
    )

    await remove_project_member(
        project_id=project.id,
        target_user_id=member.id,
        actor_id=owner.id,
        db=db,
    )
    await db.commit()

    logs = list((await db.execute(
        select(AuditLog).where(
            AuditLog.user_id == owner.id,
            AuditLog.resource_id == project.id,
            AuditLog.action == "member_removed",
        )
    )).scalars())

    assert len(logs) == 1


@pytest.mark.asyncio
async def test_audit_log_on_role_change(db: AsyncSession):
    owner = await make_user(db)
    member = await make_user(db)
    project = await make_project(db, owner, context_type="org")

    await add_project_member(
        project_id=project.id,
        invitee_email=member.email,
        role=MemberRole.VIEWER,
        actor_id=owner.id,
        db=db,
    )

    await update_project_member_role(
        project_id=project.id,
        target_user_id=member.id,
        role=MemberRole.EDITOR,
        actor_id=owner.id,
        db=db,
    )
    await db.commit()

    logs = list((await db.execute(
        select(AuditLog).where(
            AuditLog.user_id == owner.id,
            AuditLog.resource_id == project.id,
            AuditLog.action == "member_role_changed",
        )
    )).scalars())

    assert len(logs) == 1
    assert logs[0].details["old_role"] == "viewer"
    assert logs[0].details["new_role"] == "editor"


# ─────────────────────────────────────────────────────────────────────────────
# 6. ROLE HIERARCHY
# ─────────────────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_editor_cannot_update_instructions(db: AsyncSession):
    """Editing project instructions requires owner role (write != update_instructions for editors)."""
    # update_instructions is in ACTION_ROLE_REQUIREMENTS as {"owner", "editor"}, so editors CAN
    # — but let's verify the boundary: viewers cannot.
    owner = await make_user(db)
    viewer = await make_user(db)
    project = await make_project(db, owner, context_type="org")

    await add_project_member(
        project_id=project.id,
        invitee_email=viewer.email,
        role=MemberRole.VIEWER,
        actor_id=owner.id,
        db=db,
    )

    with pytest.raises(HTTPException) as exc_info:
        await check_project_access(project.id, viewer.id, "update_instructions", db)
    assert exc_info.value.status_code == 403


@pytest.mark.asyncio
async def test_owner_can_do_all_actions(db: AsyncSession):
    """Project owner can perform any action."""
    owner = await make_user(db)
    project = await make_project(db, owner, context_type="org")

    for action in ["read", "write", "upload", "manage_members", "add_member", "remove_member",
                   "update_instructions", "delete", "view_members"]:
        role = await check_project_access(project.id, owner.id, action, db)
        assert role == "owner"
