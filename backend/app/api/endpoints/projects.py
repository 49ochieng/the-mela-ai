"""
Mela AI - Projects Endpoints
"""

import logging
from fastapi import APIRouter, Depends, HTTPException, status, UploadFile, File, Query
from typing import Optional
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.core.security import get_current_user
from app.core.profile_context import get_optional_profile_context, ProfileContext
from app.schemas.auth import UserInfo
from app.schemas.projects import (
    ProjectCreate,
    ProjectUpdate,
    ProjectResponse,
    ProjectDetail,
    ProjectMemoryItem,
    AddMemoryRequest,
    ProjectFileResponse,
    ProjectInstructionsUpdate,
    ProjectConversationResponse,
)
import app.services.project_service as project_service

logger = logging.getLogger(__name__)
router = APIRouter()


def _not_found(e: Exception) -> HTTPException:
    return HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(e))


# ── Shared views ──────────────────────────────────────────────────────────────

@router.get("/shared-with-me", response_model=list[ProjectResponse])
async def list_shared_with_me_projects(
    current_user: UserInfo = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
    profile_ctx: ProfileContext = Depends(get_optional_profile_context),
):
    """Projects explicitly shared with the current user by a colleague."""
    return await project_service.list_shared_with_me_projects(
        current_user.id, db, profile_ctx=profile_ctx,
    )


@router.get("/shared-by-me", response_model=list[ProjectResponse])
async def list_shared_by_me_projects(
    current_user: UserInfo = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
    profile_ctx: ProfileContext = Depends(get_optional_profile_context),
):
    """Projects owned by the current user that have been shared with others."""
    return await project_service.list_shared_by_me_projects(
        current_user.id, db, profile_ctx=profile_ctx,
    )


# ── Project CRUD ──────────────────────────────────────────────────────────────

@router.get("", response_model=list[ProjectResponse])
async def list_projects(
    include_archived: bool = False,
    context_type: Optional[str] = Query(default=None, description="Filter by 'org'/'work' or 'personal'"),
    current_user: UserInfo = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
    profile_ctx: ProfileContext = Depends(get_optional_profile_context),
):
    return await project_service.list_projects(
        current_user.id, db, include_archived, context_type, profile_context=profile_ctx
    )


@router.post("", response_model=ProjectResponse, status_code=status.HTTP_201_CREATED)
async def create_project(
    data: ProjectCreate,
    current_user: UserInfo = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
    profile_ctx: ProfileContext = Depends(get_optional_profile_context),
):
    return await project_service.create_project(current_user.id, data, db, profile_context=profile_ctx)


@router.get("/{project_id}", response_model=ProjectDetail)
async def get_project(
    project_id: str,
    current_user: UserInfo = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
    profile_ctx: ProfileContext = Depends(get_optional_profile_context),
):
    try:
        result = await project_service.get_project(project_id, current_user.id, db)
    except ValueError as e:
        raise _not_found(e)
    # Validate fetched record against caller's profile/tenant boundary.
    profile_ctx.validate_record(result)
    return result


@router.put("/{project_id}", response_model=ProjectResponse)
async def update_project(
    project_id: str,
    data: ProjectUpdate,
    current_user: UserInfo = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    try:
        return await project_service.update_project(project_id, current_user.id, data, db)
    except ValueError as e:
        raise _not_found(e)


@router.delete("/{project_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_project(
    project_id: str,
    current_user: UserInfo = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    try:
        await project_service.delete_project(project_id, current_user.id, db)
    except ValueError as e:
        raise _not_found(e)


# ── Conversation assignment ───────────────────────────────────────────────────

@router.post("/{project_id}/conversations/{conv_id}", status_code=status.HTTP_204_NO_CONTENT)
async def assign_conversation(
    project_id: str,
    conv_id: str,
    current_user: UserInfo = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    try:
        await project_service.assign_conversation(project_id, conv_id, current_user.id, db)
    except PermissionError as e:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(e))
    except ValueError as e:
        raise _not_found(e)


@router.delete("/{project_id}/conversations/{conv_id}", status_code=status.HTTP_204_NO_CONTENT)
async def unassign_conversation(
    project_id: str,
    conv_id: str,
    current_user: UserInfo = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    try:
        await project_service.unassign_conversation(conv_id, current_user.id, db)
    except ValueError as e:
        raise _not_found(e)


# ── Memory management ─────────────────────────────────────────────────────────

@router.post("/{project_id}/memories", response_model=ProjectMemoryItem, status_code=status.HTTP_201_CREATED)
async def add_memory(
    project_id: str,
    body: AddMemoryRequest,
    current_user: UserInfo = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    # Verify ownership
    try:
        await project_service.get_project(project_id, current_user.id, db)
    except ValueError as e:
        raise _not_found(e)
    return await project_service.add_memory(project_id, body.fact, None, db)


@router.delete("/{project_id}/memories/{memory_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_memory(
    project_id: str,
    memory_id: str,
    current_user: UserInfo = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    # Verify project ownership
    try:
        await project_service.get_project(project_id, current_user.id, db)
    except ValueError as e:
        raise _not_found(e)
    await project_service.delete_memory(memory_id, project_id, db)


# ── Instructions ──────────────────────────────────────────────────────────────

@router.get("/{project_id}/instructions")
async def get_instructions(
    project_id: str,
    current_user: UserInfo = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    try:
        project = await project_service.get_project(project_id, current_user.id, db)
        return {"system_prompt": project.system_prompt or ""}
    except ValueError as e:
        raise _not_found(e)


@router.put("/{project_id}/instructions")
async def update_instructions(
    project_id: str,
    body: ProjectInstructionsUpdate,
    current_user: UserInfo = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    try:
        return await project_service.update_project(
            project_id,
            current_user.id,
            ProjectUpdate(system_prompt=body.system_prompt or None),
            db,
        )
    except ValueError as e:
        raise _not_found(e)


# ── Conversations ─────────────────────────────────────────────────────────────

@router.get("/{project_id}/conversations", response_model=list[ProjectConversationResponse])
async def list_project_conversations(
    project_id: str,
    current_user: UserInfo = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    try:
        return await project_service.list_project_conversations(project_id, current_user.id, db)
    except ValueError as e:
        raise _not_found(e)


# ── Files ─────────────────────────────────────────────────────────────────────

@router.get("/{project_id}/files", response_model=list[ProjectFileResponse])
async def list_files(
    project_id: str,
    current_user: UserInfo = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    try:
        return await project_service.list_project_files(project_id, current_user.id, db)
    except ValueError as e:
        raise _not_found(e)


@router.post("/{project_id}/files", response_model=ProjectFileResponse, status_code=status.HTTP_201_CREATED)
async def upload_file(
    project_id: str,
    file: UploadFile = File(...),
    current_user: UserInfo = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Upload a file to a project, extracting text content."""
    try:
        contents = await file.read()
        file_size = len(contents)

        # Extract text from the file
        content_text: str | None = None
        try:
            from app.services.document_service import get_document_processor
            doc_proc = get_document_processor()
            if doc_proc:
                content_type = file.content_type or "application/octet-stream"
                extracted, _ = doc_proc.extract_text(
                    contents, content_type, file.filename or "file"
                )
                content_text = extracted or None
        except Exception as ex:
            logger.warning(f"Text extraction failed for project file: {ex}")

        return await project_service.add_project_file(
            project_id=project_id,
            user_id=current_user.id,
            filename=file.filename or "file",
            file_type=file.content_type or "application/octet-stream",
            file_size=file_size,
            content_text=content_text,
            db=db,
        )
    except ValueError as e:
        raise _not_found(e)


@router.delete("/{project_id}/files/{file_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_file(
    project_id: str,
    file_id: str,
    current_user: UserInfo = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    try:
        await project_service.delete_project_file(project_id, file_id, current_user.id, db)
    except ValueError as e:
        raise _not_found(e)
