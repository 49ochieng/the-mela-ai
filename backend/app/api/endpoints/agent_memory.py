"""
Mela AI - Agent Memory Endpoints

REST surface for the Agent Memory feature:

    POST   /agent-memory/upload                  multipart file upload
    POST   /agent-memory/web                     add a website URL
    GET    /agent-memory/items                   list items (own + tenant-shared)
    GET    /agent-memory/items/{id}              fetch one
    DELETE /agent-memory/items/{id}              owner-only
    POST   /agent-memory/items/{id}/reindex      owner-only
    PATCH  /agent-memory/items/{id}/session      mute/unmute for one conversation
    GET    /agent-memory/templates               list ready templates
"""

from __future__ import annotations

import logging
from typing import List, Optional

from fastapi import (
    APIRouter, Depends, File, Form, HTTPException, UploadFile, status,
)
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.core.security import get_current_user
from app.models.models import AgentMemoryItem, User, UserRole
from app.schemas.auth import UserInfo
from app.services.agent_memory_service import (
    AgentMemoryError,
    ForbiddenError,
    ItemNotFoundError,
    agent_memory_service,
)

logger = logging.getLogger(__name__)
router = APIRouter()


# ── DTOs ─────────────────────────────────────────────────────────────────────


class AgentMemoryItemDTO(BaseModel):
    id: str
    user_id: str
    tenant_id: Optional[str] = None
    scope: str
    tag: str
    source_type: str
    title: str
    url: Optional[str] = None
    file_type: Optional[str] = None
    file_size: Optional[int] = None
    status: str
    error_message: Optional[str] = None
    chunk_count: int
    page_count: int
    has_template_schema: bool = False
    last_synced_at: Optional[str] = None
    created_at: str
    updated_at: str

    @classmethod
    def from_orm_row(cls, item: AgentMemoryItem) -> "AgentMemoryItemDTO":
        return cls(
            id=item.id,
            user_id=item.user_id,
            tenant_id=item.tenant_id,
            scope=item.scope,
            tag=item.tag,
            source_type=item.source_type,
            title=item.title,
            url=item.url,
            file_type=item.file_type,
            file_size=item.file_size,
            status=item.status,
            error_message=item.error_message,
            chunk_count=item.chunk_count,
            page_count=item.page_count,
            has_template_schema=bool(item.template_schema_json),
            last_synced_at=item.last_synced_at.isoformat() if item.last_synced_at else None,
            created_at=item.created_at.isoformat(),
            updated_at=item.updated_at.isoformat(),
        )


class WebAddRequest(BaseModel):
    url: str = Field(..., min_length=4, max_length=2000)
    scope: str = Field(default="personal")
    tag: str = Field(default="knowledge")
    title: Optional[str] = Field(default=None, max_length=500)


class SessionToggleRequest(BaseModel):
    conversation_id: str
    disabled: bool


class ListResponse(BaseModel):
    items: List[AgentMemoryItemDTO]
    total: int


# ── Helpers ──────────────────────────────────────────────────────────────────


_MAX_UPLOAD_BYTES = 25 * 1024 * 1024  # 25 MiB hard cap on a single file


async def _load_user(db: AsyncSession, current_user: UserInfo) -> User:
    """Resolve the local User row from the JWT identity.

    `current_user.id` is the Entra/dev `oid`, not the local DB primary key.
    Look up by `azure_id`; fall back to email; auto-create on first hit so
    users that have not visited /auth/login yet still work.
    """
    # Primary: azure_id match
    result = await db.execute(select(User).where(User.azure_id == current_user.id))
    user = result.scalar_one_or_none()

    # Fallback: email match (handles dev → real OID migrations)
    if user is None and current_user.email:
        result = await db.execute(
            select(User).where(func.lower(User.email) == current_user.email.lower())
        )
        user = result.scalar_one_or_none()
        if user is not None:
            user.azure_id = current_user.id
            await db.flush()
            logger.info(
                "agent_memory: migrated azure_id for %s → %s",
                current_user.email, current_user.id,
            )

    # Auto-create on first call
    if user is None:
        user = User(
            azure_id=current_user.id,
            email=current_user.email,
            name=current_user.name,
            department=current_user.department,
            job_title=current_user.job_title,
            role=UserRole.USER,
        )
        db.add(user)
        try:
            await db.flush()
            logger.info(
                "agent_memory: auto-created user row for %s (oid=%s)",
                current_user.email, current_user.id,
            )
        except Exception as exc:
            await db.rollback()
            logger.warning("agent_memory: user auto-create failed: %s", exc)
            # Re-fetch in case of unique-constraint race
            result = await db.execute(
                select(User).where(User.azure_id == current_user.id)
            )
            user = result.scalar_one_or_none()
            if user is None:
                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND,
                    detail="User record not found",
                ) from exc
    return user


def _wrap(exc: Exception) -> HTTPException:
    if isinstance(exc, ForbiddenError):
        return HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(exc))
    if isinstance(exc, ItemNotFoundError):
        return HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc))
    if isinstance(exc, AgentMemoryError):
        return HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc))
    return HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=str(exc))


# ── Endpoints ────────────────────────────────────────────────────────────────


@router.post("/upload", response_model=AgentMemoryItemDTO)
async def upload_item(
    file: UploadFile = File(...),
    scope: str = Form("personal"),
    tag: str = Form("knowledge"),
    title: Optional[str] = Form(None),
    current_user: UserInfo = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    user = await _load_user(db, current_user)
    data = await file.read()
    if len(data) == 0:
        raise HTTPException(status_code=400, detail="empty file")
    if len(data) > _MAX_UPLOAD_BYTES:
        raise HTTPException(
            status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            detail=f"file too large: {len(data)} bytes (max {_MAX_UPLOAD_BYTES})",
        )
    try:
        item = await agent_memory_service.create_from_upload(
            db=db,
            user=user,
            scope=scope,
            tag=tag,
            tenant_id=current_user.tenant_id,
            filename=file.filename or "upload.bin",
            content_type=file.content_type or "",
            data=data,
            title=title,
        )
    except Exception as exc:
        raise _wrap(exc) from exc
    return AgentMemoryItemDTO.from_orm_row(item)


@router.post("/web", response_model=AgentMemoryItemDTO)
async def add_web_item(
    body: WebAddRequest,
    current_user: UserInfo = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    user = await _load_user(db, current_user)
    try:
        item = await agent_memory_service.create_from_url(
            db=db,
            user=user,
            scope=body.scope,
            tag=body.tag,
            tenant_id=current_user.tenant_id,
            url=body.url,
            title=body.title,
        )
    except Exception as exc:
        raise _wrap(exc) from exc
    return AgentMemoryItemDTO.from_orm_row(item)


@router.get("/items", response_model=ListResponse)
async def list_items(
    scope: Optional[str] = None,
    tag: Optional[str] = None,
    current_user: UserInfo = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    user = await _load_user(db, current_user)
    try:
        rows = await agent_memory_service.list_items(
            db, user,
            tenant_id=current_user.tenant_id,
            scope=scope, tag=tag,
        )
    except Exception as exc:
        raise _wrap(exc) from exc
    items = [AgentMemoryItemDTO.from_orm_row(r) for r in rows]
    return ListResponse(items=items, total=len(items))


@router.get("/items/{item_id}", response_model=AgentMemoryItemDTO)
async def get_item(
    item_id: str,
    current_user: UserInfo = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    user = await _load_user(db, current_user)
    try:
        item = await agent_memory_service.get_item(
            db, user, item_id, tenant_id=current_user.tenant_id,
        )
    except Exception as exc:
        raise _wrap(exc) from exc
    return AgentMemoryItemDTO.from_orm_row(item)


@router.delete("/items/{item_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_item(
    item_id: str,
    current_user: UserInfo = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    user = await _load_user(db, current_user)
    try:
        await agent_memory_service.delete_item(db, user, item_id)
    except Exception as exc:
        raise _wrap(exc) from exc
    return None


@router.post("/items/{item_id}/reindex", response_model=AgentMemoryItemDTO)
async def reindex_item(
    item_id: str,
    current_user: UserInfo = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    user = await _load_user(db, current_user)
    try:
        item = await agent_memory_service.reindex(db, user, item_id)
    except Exception as exc:
        raise _wrap(exc) from exc
    return AgentMemoryItemDTO.from_orm_row(item)


@router.patch("/items/{item_id}/session", response_model=AgentMemoryItemDTO)
async def toggle_session_disabled(
    item_id: str,
    body: SessionToggleRequest,
    current_user: UserInfo = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    user = await _load_user(db, current_user)
    try:
        item = await agent_memory_service.set_session_disabled(
            db, user, item_id, body.conversation_id, body.disabled,
        )
    except Exception as exc:
        raise _wrap(exc) from exc
    return AgentMemoryItemDTO.from_orm_row(item)


@router.get("/templates", response_model=ListResponse)
async def list_templates(
    current_user: UserInfo = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    user = await _load_user(db, current_user)
    try:
        rows = await agent_memory_service.list_items(
            db, user,
            tenant_id=current_user.tenant_id,
            tag="template",
        )
    except Exception as exc:
        raise _wrap(exc) from exc
    items = [
        AgentMemoryItemDTO.from_orm_row(r) for r in rows if r.status == "ready"
    ]
    return ListResponse(items=items, total=len(items))
