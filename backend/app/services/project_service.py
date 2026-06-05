"""
Mela AI - Project Service
"""

import logging
from datetime import datetime
from typing import Optional
from sqlalchemy import select, func, update, delete
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.models import Project, ProjectMemory, ProjectFile, Conversation
from app.schemas.projects import (
    ProjectCreate,
    ProjectUpdate,
    ProjectResponse,
    ProjectDetail,
    ProjectMemoryItem,
    ProjectFileResponse,
    ProjectConversationResponse,
)

logger = logging.getLogger(__name__)

MAX_MEMORIES_PER_PROJECT = 50


async def list_projects(
    user_id: str,
    db: AsyncSession,
    include_archived: bool = False,
    context_type: Optional[str] = None,
    profile_context=None,  # app.core.profile_context.ProfileContext | None
) -> list[ProjectResponse]:
    """Return all projects for a user (owned + member-of) with conversation counts."""
    from app.models.models import ProjectMember

    # Subquery: count conversations per project
    conv_count_sq = (
        select(
            Conversation.project_id,
            func.count(Conversation.id).label("cnt"),
        )
        .where(Conversation.project_id.isnot(None))
        .group_by(Conversation.project_id)
        .subquery()
    )

    # Projects owned by user OR where user is a member
    owned_ids_sq = select(Project.id).where(Project.user_id == user_id)
    member_ids_sq = select(ProjectMember.project_id).where(ProjectMember.user_id == user_id)

    stmt = (
        select(Project, func.coalesce(conv_count_sq.c.cnt, 0).label("conversation_count"))
        .outerjoin(conv_count_sq, Project.id == conv_count_sq.c.project_id)
        .where(
            (Project.id.in_(owned_ids_sq)) | (Project.id.in_(member_ids_sq))
        )
        .order_by(Project.created_at.desc())
    )
    if not include_archived:
        stmt = stmt.where(Project.is_archived == False)

    # GDPR/SOC2 Sprint 2: hide soft-deleted projects when the flag is on.
    from app.core.soft_delete import filter_deleted
    stmt = filter_deleted(stmt, Project)

    # Apply profile namespace filter — ProfileContext takes precedence
    if profile_context is not None:
        stmt = stmt.where(*profile_context.where_clauses(Project))
    elif context_type:
        _pm = "work" if context_type == "org" else context_type
        stmt = stmt.where(Project.profile_mode == _pm)

    rows = (await db.execute(stmt)).all()
    result = []
    for project, count in rows:
        r = ProjectResponse.model_validate(project)
        r.conversation_count = count
        result.append(r)
    return result


async def get_project(
    project_id: str,
    user_id: str,
    db: AsyncSession,
) -> ProjectDetail:
    """Return project detail with memories (for owner or member)."""
    from app.models.models import ProjectMember

    project = await db.scalar(select(Project).where(Project.id == project_id))
    if not project:
        raise ValueError("Project not found")

    # Access check: must be owner or member
    if project.user_id != user_id:
        member = await db.scalar(
            select(ProjectMember).where(
                ProjectMember.project_id == project_id,
                ProjectMember.user_id == user_id,
            )
        )
        if not member:
            raise ValueError("Project not found")

    memories = list(
        (
            await db.execute(
                select(ProjectMemory)
                .where(ProjectMemory.project_id == project_id)
                .order_by(ProjectMemory.created_at.desc())
            )
        ).scalars()
    )

    conv_count = await db.scalar(
        select(func.count(Conversation.id)).where(Conversation.project_id == project_id)
    ) or 0

    # Build dict from scalar columns only to avoid triggering lazy-load of
    # the `memories` relationship inside Pydantic's model_validate, which
    # would raise a greenlet error in an async SQLAlchemy session.
    from sqlalchemy import inspect as _sa_inspect
    proj_dict: dict = {
        c.key: getattr(project, c.key)
        for c in _sa_inspect(project.__class__).mapper.column_attrs
    }
    proj_dict["conversation_count"] = conv_count
    proj_dict["memories"] = [
        ProjectMemoryItem.model_validate(m) for m in memories
    ]
    return ProjectDetail.model_validate(proj_dict)


async def create_project(
    user_id: str,
    data: ProjectCreate,
    db: AsyncSession,
    profile_context=None,  # app.core.profile_context.ProfileContext | None
) -> ProjectResponse:
    """Create a new project."""
    # Resolve profile_mode — server is authoritative when profile_context is provided
    if profile_context is not None:
        profile_mode = profile_context.profile_mode
        tenant_id = profile_context.db_tenant_id
    else:
        raw_ctx = getattr(data, "context_type", "personal") or "personal"
        profile_mode = "work" if raw_ctx == "org" else raw_ctx
        tenant_id = getattr(data, "tenant_id", None)
        if profile_mode == "personal":
            tenant_id = None
    project = Project(
        user_id=user_id,
        name=data.name,
        description=data.description,
        icon=data.icon,
        color=data.color,
        system_prompt=data.system_prompt,
        profile_mode=profile_mode,
        tenant_id=tenant_id,
        context_type=getattr(data, "context_type", profile_mode),
        workspace_id=getattr(data, "workspace_id", None),
    )
    db.add(project)
    await db.commit()
    await db.refresh(project)
    r = ProjectResponse.model_validate(project)
    r.conversation_count = 0
    return r


async def update_project(
    project_id: str,
    user_id: str,
    data: ProjectUpdate,
    db: AsyncSession,
) -> ProjectResponse:
    """Update an existing project."""
    project = await db.scalar(
        select(Project).where(Project.id == project_id, Project.user_id == user_id)
    )
    if not project:
        raise ValueError("Project not found")

    for field, value in data.model_dump(exclude_unset=True).items():
        setattr(project, field, value)
    project.updated_at = datetime.utcnow()
    await db.commit()
    await db.refresh(project)

    conv_count = await db.scalar(
        select(func.count(Conversation.id)).where(Conversation.project_id == project_id)
    ) or 0
    r = ProjectResponse.model_validate(project)
    r.conversation_count = conv_count
    return r


async def delete_project(
    project_id: str,
    user_id: str,
    db: AsyncSession,
) -> None:
    """Delete a project, unlinking all its conversations."""
    project = await db.scalar(
        select(Project).where(Project.id == project_id, Project.user_id == user_id)
    )
    if not project:
        raise ValueError("Project not found")

    # Unlink conversations before deleting
    await db.execute(
        update(Conversation)
        .where(Conversation.project_id == project_id)
        .values(project_id=None)
    )
    await db.delete(project)
    await db.commit()


async def assign_conversation(
    project_id: str,
    conversation_id: str,
    user_id: str,
    db: AsyncSession,
) -> None:
    """Assign a conversation to a project.  Requires at least editor role on the project.
    Private chats are unconditionally blocked."""
    from app.core.authorization import check_project_access
    from fastapi import HTTPException

    # Verify the user has write access to the project (owner or editor)
    try:
        await check_project_access(project_id, user_id, "write", db)
    except HTTPException:
        raise ValueError("Project not found or insufficient permissions")

    # Load project (now we know user has access)
    project = await db.scalar(select(Project).where(Project.id == project_id))
    if not project:
        raise ValueError("Project not found")

    # The conversation must belong to this user (you can only move your own chats)
    conv = await db.scalar(
        select(Conversation).where(
            Conversation.id == conversation_id,
            Conversation.user_id == user_id,
        )
    )
    if not conv:
        raise ValueError("Conversation not found")

    # Non-negotiable: private chats cannot join shared projects
    if conv.is_private:
        raise PermissionError("Private chats cannot be moved into shared projects")

    conv.project_id = project_id
    await db.commit()


async def unassign_conversation(
    conversation_id: str,
    user_id: str,
    db: AsyncSession,
) -> None:
    """Remove a conversation from its project."""
    conv = await db.scalar(
        select(Conversation).where(
            Conversation.id == conversation_id,
            Conversation.user_id == user_id,
        )
    )
    if not conv:
        raise ValueError("Conversation not found")

    conv.project_id = None
    await db.commit()


async def add_memory(
    project_id: str,
    fact: str,
    source_conversation_id: Optional[str],
    db: AsyncSession,
) -> ProjectMemoryItem:
    """Add a memory fact; enforce 50-item cap by dropping the oldest."""
    mem = ProjectMemory(
        project_id=project_id,
        fact=fact.strip(),
        source_conversation_id=source_conversation_id,
    )
    db.add(mem)
    await db.flush()  # get the new row's id without committing yet

    # Enforce cap
    total = await db.scalar(
        select(func.count(ProjectMemory.id)).where(ProjectMemory.project_id == project_id)
    ) or 0

    if total > MAX_MEMORIES_PER_PROJECT:
        oldest_ids = list(
            (
                await db.execute(
                    select(ProjectMemory.id)
                    .where(ProjectMemory.project_id == project_id)
                    .order_by(ProjectMemory.created_at.asc())
                    .limit(total - MAX_MEMORIES_PER_PROJECT)
                )
            ).scalars()
        )
        if oldest_ids:
            await db.execute(
                delete(ProjectMemory).where(ProjectMemory.id.in_(oldest_ids))
            )

    await db.commit()
    await db.refresh(mem)
    return ProjectMemoryItem.model_validate(mem)


async def delete_memory(
    memory_id: str,
    project_id: str,
    db: AsyncSession,
) -> None:
    """Delete a specific memory fact."""
    await db.execute(
        delete(ProjectMemory).where(
            ProjectMemory.id == memory_id,
            ProjectMemory.project_id == project_id,
        )
    )
    await db.commit()


# ── Project Files ─────────────────────────────────────────────────────────────

async def list_project_files(
    project_id: str,
    user_id: str,
    db: AsyncSession,
) -> list[ProjectFileResponse]:
    """List all files for a project (owner or any member may list)."""
    from app.core.authorization import check_project_access
    from fastapi import HTTPException
    try:
        await check_project_access(project_id, user_id, "read", db)
    except HTTPException:
        raise ValueError("Project not found")
    project = await db.scalar(select(Project).where(Project.id == project_id))
    if not project:
        raise ValueError("Project not found")

    files = list(
        (
            await db.execute(
                select(ProjectFile)
                .where(ProjectFile.project_id == project_id)
                .order_by(ProjectFile.created_at.desc())
            )
        ).scalars()
    )
    return [ProjectFileResponse.model_validate(f) for f in files]


async def add_project_file(
    project_id: str,
    user_id: str,
    filename: str,
    file_type: str,
    file_size: int,
    content_text: Optional[str],
    db: AsyncSession,
) -> ProjectFileResponse:
    """Add a file record to a project (owner or editor may upload)."""
    from app.core.authorization import check_project_access
    from fastapi import HTTPException
    try:
        await check_project_access(project_id, user_id, "upload", db)
    except HTTPException:
        raise ValueError("Project not found or insufficient permissions")
    project = await db.scalar(select(Project).where(Project.id == project_id))
    if not project:
        raise ValueError("Project not found")

    pf = ProjectFile(
        project_id=project_id,
        filename=filename,
        file_type=file_type,
        file_size=file_size,
        content_text=content_text,
        uploaded_by=user_id,
    )
    db.add(pf)
    await db.commit()
    await db.refresh(pf)
    return ProjectFileResponse.model_validate(pf)


async def delete_project_file(
    project_id: str,
    file_id: str,
    user_id: str,
    db: AsyncSession,
) -> None:
    """Delete a project file (owner or editor)."""
    from app.core.authorization import check_project_access
    from fastapi import HTTPException
    try:
        await check_project_access(project_id, user_id, "upload", db)
    except HTTPException:
        raise ValueError("Project not found or insufficient permissions")
    project = await db.scalar(select(Project).where(Project.id == project_id))
    if not project:
        raise ValueError("Project not found")

    await db.execute(
        delete(ProjectFile).where(
            ProjectFile.id == file_id,
            ProjectFile.project_id == project_id,
        )
    )
    await db.commit()


async def get_project_file_texts(
    project_id: str,
    db: AsyncSession,
    max_chars_per_file: int = 15000,
) -> list[dict]:
    """Return extracted text from all project files (for RAG injection)."""
    files = list(
        (
            await db.execute(
                select(ProjectFile)
                .where(
                    ProjectFile.project_id == project_id,
                    ProjectFile.content_text.isnot(None),
                )
                .order_by(ProjectFile.created_at.asc())
            )
        ).scalars()
    )
    result = []
    for f in files:
        text = (f.content_text or "").strip()
        if text:
            result.append({
                "filename": f.filename,
                "content": text[:max_chars_per_file],
            })
    return result


# ── Project Conversations ──────────────────────────────────────────────────────

async def list_project_conversations(
    project_id: str,
    user_id: str,
    db: AsyncSession,
) -> list[ProjectConversationResponse]:
    """List all conversations in a project with message counts.
    Returns conversations from all members — access is verified first."""
    # Verify access (owner or member)
    project = await db.scalar(select(Project).where(Project.id == project_id))
    if not project:
        raise ValueError("Project not found")
    from app.models.models import ProjectMember
    if project.user_id != user_id:
        member = await db.scalar(
            select(ProjectMember).where(
                ProjectMember.project_id == project_id,
                ProjectMember.user_id == user_id,
            )
        )
        if not member:
            raise ValueError("Project not found")

    from app.models.models import Message
    msg_count_sq = (
        select(
            Message.conversation_id,
            func.count(Message.id).label("cnt"),
        )
        .group_by(Message.conversation_id)
        .subquery()
    )

    rows = list(
        (
            await db.execute(
                select(Conversation, func.coalesce(msg_count_sq.c.cnt, 0).label("message_count"))
                .outerjoin(msg_count_sq, Conversation.id == msg_count_sq.c.conversation_id)
                .where(
                    Conversation.project_id == project_id,
                    Conversation.is_archived == False,
                )
                .order_by(Conversation.updated_at.desc())
            )
        ).all()
    )

    result = []
    for conv, count in rows:
        r = ProjectConversationResponse.model_validate(conv)
        r.message_count = count
        result.append(r)
    return result


# ── Shared projects ────────────────────────────────────────────────────────────

async def list_shared_with_me_projects(
    user_id: str,
    db: AsyncSession,
    profile_ctx=None,
) -> list[ProjectResponse]:
    """Projects shared with this user (they are a member, not the owner)."""
    from app.models.models import ProjectMember

    conv_count_sq = (
        select(Conversation.project_id, func.count(Conversation.id).label("cnt"))
        .where(Conversation.project_id.isnot(None))
        .group_by(Conversation.project_id)
        .subquery()
    )

    stmt = (
        select(Project, func.coalesce(conv_count_sq.c.cnt, 0).label("conversation_count"))
        .join(ProjectMember, ProjectMember.project_id == Project.id)
        .outerjoin(conv_count_sq, Project.id == conv_count_sq.c.project_id)
        .where(
            ProjectMember.user_id == user_id,
            Project.user_id != user_id,
            Project.is_archived == False,  # noqa: E712
        )
        .order_by(Project.created_at.desc())
    )
    if profile_ctx is not None:
        stmt = stmt.where(*profile_ctx.where_clauses(Project))
    rows = (await db.execute(stmt)).all()

    result = []
    for project, count in rows:
        r = ProjectResponse.model_validate(project)
        r.conversation_count = count
        result.append(r)
    return result


async def list_shared_by_me_projects(
    user_id: str,
    db: AsyncSession,
    profile_ctx=None,
) -> list[ProjectResponse]:
    """Projects owned by this user that have at least one other member."""
    from app.models.models import ProjectMember
    from sqlalchemy import exists

    has_members = (
        select(ProjectMember.id)
        .where(
            ProjectMember.project_id == Project.id,
            ProjectMember.user_id != user_id,
        )
        .limit(1)
    )

    conv_count_sq = (
        select(Conversation.project_id, func.count(Conversation.id).label("cnt"))
        .where(Conversation.project_id.isnot(None))
        .group_by(Conversation.project_id)
        .subquery()
    )

    stmt = (
        select(Project, func.coalesce(conv_count_sq.c.cnt, 0).label("conversation_count"))
        .outerjoin(conv_count_sq, Project.id == conv_count_sq.c.project_id)
        .where(
            Project.user_id == user_id,
            Project.is_archived == False,  # noqa: E712
            exists(has_members),
        )
        .order_by(Project.created_at.desc())
    )
    if profile_ctx is not None:
        stmt = stmt.where(*profile_ctx.where_clauses(Project))
    rows = (await db.execute(stmt)).all()

    result = []
    for project, count in rows:
        r = ProjectResponse.model_validate(project)
        r.conversation_count = count
        result.append(r)
    return result
