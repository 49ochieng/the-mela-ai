"""
Mela AI - Memory Management Endpoints

Provides API endpoints for users to view and manage their memories:
- GET /memories - List user's long-term memories
- POST /memories - Add a new memory manually
- DELETE /memories/{id} - Remove a memory
- GET /memories/session/{conversation_id} - Get session memory for a chat
"""

import logging
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.core.security import get_current_user
from app.models.models import MemoryType
from app.schemas.auth import UserInfo


logger = logging.getLogger(__name__)
router = APIRouter()


# ─────────────────────────────────────────────────────────────────────────────
# Request/Response Schemas
# ─────────────────────────────────────────────────────────────────────────────

class MemoryResponse(BaseModel):
    """Response schema for a single memory."""
    id: str
    content: str
    memory_type: str
    category: Optional[str] = None
    relevance_score: int
    usage_count: int
    is_active: bool
    profile_scope: str
    created_at: str
    updated_at: str


class MemoryListResponse(BaseModel):
    """Response schema for memory list."""
    memories: list[MemoryResponse]
    total: int


class CreateMemoryRequest(BaseModel):
    """Request to create a new memory."""
    content: str = Field(..., min_length=1, max_length=2000)
    memory_type: str = Field(default="fact")
    category: Optional[str] = Field(default=None, max_length=50)
    profile_scope: str = Field(default="global")


class SessionMemoryResponse(BaseModel):
    """Response schema for session memory."""
    conversation_id: str
    summary: str
    key_facts: Optional[list[str]] = None
    goals: Optional[list[str]] = None
    entities: Optional[list[str]] = None
    token_count: int
    message_count: int
    expires_at: str


# ─────────────────────────────────────────────────────────────────────────────
# Endpoints
# ─────────────────────────────────────────────────────────────────────────────

@router.get("", response_model=MemoryListResponse)
async def list_memories(
    current_user: UserInfo = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
    limit: int = 50,
    include_inactive: bool = False,
):
    """List all long-term memories for the current user."""
    from app.services.memory_service import memory_service
    from sqlalchemy import select
    from app.models.models import UserMemory

    # Build query
    stmt = select(UserMemory).where(UserMemory.user_id == str(current_user.id))

    if not include_inactive:
        stmt = stmt.where(UserMemory.is_active.is_(True))

    stmt = stmt.order_by(
        UserMemory.relevance_score.desc(),
        UserMemory.updated_at.desc(),
    ).limit(limit)

    result = await db.execute(stmt)
    memories = result.scalars().all()

    return MemoryListResponse(
        memories=[
            MemoryResponse(
                id=m.id,
                content=m.content,
                memory_type=m.memory_type.value,
                category=m.category,
                relevance_score=m.relevance_score,
                usage_count=m.usage_count,
                is_active=m.is_active,
                profile_scope=m.profile_scope,
                created_at=m.created_at.isoformat(),
                updated_at=m.updated_at.isoformat(),
            )
            for m in memories
        ],
        total=len(memories),
    )


@router.post("", response_model=MemoryResponse, status_code=status.HTTP_201_CREATED)
async def create_memory(
    request: CreateMemoryRequest,
    current_user: UserInfo = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Manually add a new long-term memory."""
    from app.services.memory_service import memory_service

    # Map string to enum
    type_map = {
        "preference": MemoryType.PREFERENCE,
        "correction": MemoryType.CORRECTION,
        "fact": MemoryType.FACT,
        "context": MemoryType.CONTEXT,
        "style": MemoryType.STYLE,
    }
    memory_type = type_map.get(request.memory_type.lower(), MemoryType.FACT)

    memory = await memory_service.add_long_term_memory(
        db=db,
        user_id=str(current_user.id),
        content=request.content,
        memory_type=memory_type,
        category=request.category,
        profile_scope=request.profile_scope,
    )

    return MemoryResponse(
        id=memory.id,
        content=memory.content,
        memory_type=memory.memory_type.value,
        category=memory.category,
        relevance_score=memory.relevance_score,
        usage_count=memory.usage_count,
        is_active=memory.is_active,
        profile_scope=memory.profile_scope,
        created_at=memory.created_at.isoformat(),
        updated_at=memory.updated_at.isoformat(),
    )


@router.delete("/{memory_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_memory(
    memory_id: str,
    current_user: UserInfo = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
    hard_delete: bool = False,
):
    """Delete a memory. By default, soft-deletes (deactivates)."""
    from app.services.memory_service import memory_service

    if hard_delete:
        success = await memory_service.delete_memory(
            db, memory_id, str(current_user.id)
        )
    else:
        success = await memory_service.deactivate_memory(
            db, memory_id, str(current_user.id)
        )

    if not success:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Memory not found or you don't have permission to delete it.",
        )


@router.get(
    "/session/{conversation_id}",
    response_model=SessionMemoryResponse,
)
async def get_session_memory(
    conversation_id: str,
    current_user: UserInfo = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Get session memory for a specific conversation."""
    import json
    from app.services.memory_service import memory_service

    session_mem = await memory_service.get_session_memory(db, conversation_id)

    if not session_mem:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="No session memory found for this conversation.",
        )

    # Verify ownership
    if session_mem.user_id != str(current_user.id):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="You don't have permission to view this session memory.",
        )

    # Parse JSON fields
    key_facts = None
    goals = None
    entities = None

    if session_mem.key_facts:
        try:
            key_facts = json.loads(session_mem.key_facts)
        except json.JSONDecodeError:
            pass

    if session_mem.goals:
        try:
            goals = json.loads(session_mem.goals)
        except json.JSONDecodeError:
            pass

    if session_mem.entities:
        try:
            entities = json.loads(session_mem.entities)
        except json.JSONDecodeError:
            pass

    return SessionMemoryResponse(
        conversation_id=session_mem.conversation_id,
        summary=session_mem.summary,
        key_facts=key_facts,
        goals=goals,
        entities=entities,
        token_count=session_mem.token_count,
        message_count=session_mem.message_count,
        expires_at=session_mem.expires_at.isoformat(),
    )


@router.delete(
    "/session/{conversation_id}",
    status_code=status.HTTP_204_NO_CONTENT,
)
async def delete_session_memory(
    conversation_id: str,
    current_user: UserInfo = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Delete session memory for a conversation."""
    from app.services.memory_service import memory_service

    # First verify the user owns this conversation
    from sqlalchemy import select
    from app.models.models import Conversation

    stmt = select(Conversation).where(
        Conversation.id == conversation_id,
        Conversation.user_id == str(current_user.id),
    )
    result = await db.execute(stmt)
    conv = result.scalar_one_or_none()

    if not conv:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Conversation not found or you don't have permission.",
        )

    await memory_service.delete_session_memory(db, conversation_id)
