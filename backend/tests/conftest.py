"""
Test configuration and fixtures.
"""

import uuid
import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker, AsyncSession
from sqlalchemy import text

from app.core.database import Base
from app.models.models import (
    User, Conversation, Project, ProjectMember, ChatMember,
    MemberRole, UserRole,
)


# ── In-process SQLite event loop ──────────────────────────────────────────────

@pytest_asyncio.fixture(scope="function")
async def db():
    """Fresh in-memory SQLite session per test."""
    engine = create_async_engine("sqlite+aiosqlite:///:memory:", echo=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    async with session_factory() as session:
        yield session
    await engine.dispose()


# ── Helper factories ──────────────────────────────────────────────────────────

async def make_user(db: AsyncSession, email: str = None) -> User:
    uid = str(uuid.uuid4())
    user = User(
        id=uid,
        azure_id=uid,
        email=email or f"user-{uid[:8]}@test.com",
        name=f"Test {uid[:8]}",
        role=UserRole.USER,
    )
    db.add(user)
    await db.flush()
    return user


async def make_project(db: AsyncSession, owner: User, context_type: str = "personal") -> Project:
    # profile_mode is canonical ('work'|'personal'); context_type is a legacy alias ('org'→'work')
    profile_mode = "work" if context_type == "org" else context_type
    project = Project(
        id=str(uuid.uuid4()),
        user_id=owner.id,
        name="Test Project",
        context_type=context_type,
        profile_mode=profile_mode,
    )
    db.add(project)
    await db.flush()
    return project


async def make_conversation(
    db: AsyncSession,
    owner: User,
    is_private: bool = False,
    context_type: str = "personal",
) -> Conversation:
    # profile_mode is canonical ('work'|'personal'); context_type is a legacy alias ('org'→'work')
    profile_mode = "work" if context_type == "org" else context_type
    conv = Conversation(
        id=str(uuid.uuid4()),
        user_id=owner.id,
        title="Test Conversation",
        model="gpt-5.2-chat",
        is_private=is_private,
        context_type=context_type,
        profile_mode=profile_mode,
    )
    db.add(conv)
    await db.flush()
    return conv
