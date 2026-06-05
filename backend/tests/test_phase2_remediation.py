"""
Tests for Phase 2 Remediation:
A1: Memory block stripping from user-visible output
A3: Memory update/remove action processing
A4: Session memory cleanup scheduling
B1: Tool executor receives user_session for profile-aware filtering
C3: Cache hit_count increment
E1: In-memory messages cleanup in private chat
G1: Budget notification DB-backed dedup
D3: Delta token persistence
"""

import uuid
from datetime import datetime, timedelta
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import pytest_asyncio

from app.models.models import (
    MemoryType,
    NotificationType,
    Notification,
    ConnectorState,
)
from app.services.memory_service import MemoryService
from tests.conftest import make_user


# ═══════════════════════════════════════════════════════════════════════════════
# A1: Memory block stripping
# ═══════════════════════════════════════════════════════════════════════════════


class TestMemoryBlockStripping:
    def test_strip_memory_blocks_removes_blocks(self):
        svc = MemoryService()
        raw = (
            "Here is your answer.\n"
            "[MEMORY_UPDATE]\n"
            "action: add\n"
            "type: fact\n"
            "content: User likes Python\n"
            "[/MEMORY_UPDATE]\n"
            "Anything else?"
        )
        clean = svc.strip_memory_blocks(raw)
        assert "[MEMORY_UPDATE]" not in clean
        assert "[/MEMORY_UPDATE]" not in clean
        assert "Here is your answer." in clean
        assert "Anything else?" in clean

    def test_strip_memory_blocks_no_op_when_absent(self):
        svc = MemoryService()
        text = "Just a normal response with no memory blocks."
        assert svc.strip_memory_blocks(text) == text

    def test_strip_multiple_memory_blocks(self):
        svc = MemoryService()
        raw = (
            "Response.\n"
            "[MEMORY_UPDATE]\naction: add\ncontent: Fact 1\n[/MEMORY_UPDATE]\n"
            "Middle text.\n"
            "[MEMORY_UPDATE]\naction: add\ncontent: Fact 2\n[/MEMORY_UPDATE]\n"
            "End."
        )
        clean = svc.strip_memory_blocks(raw)
        assert clean.count("[MEMORY_UPDATE]") == 0
        assert "Response." in clean
        assert "Middle text." in clean
        assert "End." in clean


# ═══════════════════════════════════════════════════════════════════════════════
# A3: Memory update/remove actions
# ═══════════════════════════════════════════════════════════════════════════════


class TestMemoryUpdateRemove:
    @pytest.mark.asyncio
    async def test_process_memory_update_add(self, db):
        svc = MemoryService()
        user = await make_user(db)
        response = (
            "[MEMORY_UPDATE]\n"
            "action: add\n"
            "type: preference\n"
            "content: User prefers dark mode\n"
            "category: ui\n"
            "[/MEMORY_UPDATE]"
        )
        added = await svc.process_memory_updates(
            db=db, user_id=str(user.id), assistant_response=response,
        )
        assert len(added) == 1
        assert added[0].content == "User prefers dark mode"
        assert added[0].memory_type == MemoryType.PREFERENCE

    @pytest.mark.asyncio
    async def test_process_memory_update_action(self, db):
        """An 'update' action should modify an existing memory."""
        svc = MemoryService()
        user = await make_user(db)
        # First add a memory
        await svc.add_long_term_memory(
            db=db, user_id=str(user.id),
            content="User's favorite color is blue",
            memory_type=MemoryType.PREFERENCE,
            category="preference",
        )
        # Now update it via AI response
        response = (
            "[MEMORY_UPDATE]\n"
            "action: update\n"
            "type: preference\n"
            "content: User's favorite color is green\n"
            "target: favorite color is blue\n"
            "[/MEMORY_UPDATE]"
        )
        await svc.process_memory_updates(
            db=db, user_id=str(user.id), assistant_response=response,
        )
        # Check the memory was updated
        memories = await svc.get_long_term_memories(db, str(user.id))
        assert len(memories) == 1
        assert "green" in memories[0].content

    @pytest.mark.asyncio
    async def test_process_memory_remove_action(self, db):
        """A 'remove' action should deactivate the matching memory."""
        svc = MemoryService()
        user = await make_user(db)
        mem = await svc.add_long_term_memory(
            db=db, user_id=str(user.id),
            content="User dislikes spicy food",
            memory_type=MemoryType.FACT,
        )
        response = (
            "[MEMORY_UPDATE]\n"
            "action: remove\n"
            "content: dislikes spicy food\n"
            "[/MEMORY_UPDATE]"
        )
        await svc.process_memory_updates(
            db=db, user_id=str(user.id), assistant_response=response,
        )
        # Memory should be deactivated
        active = await svc.get_long_term_memories(db, str(user.id))
        assert len(active) == 0

    @pytest.mark.asyncio
    async def test_update_with_no_match_creates_new(self, db):
        """If no existing memory matches the update target, create a new one."""
        svc = MemoryService()
        user = await make_user(db)
        response = (
            "[MEMORY_UPDATE]\n"
            "action: update\n"
            "type: fact\n"
            "content: User works at Acme Corp\n"
            "target: nonexistent memory\n"
            "[/MEMORY_UPDATE]"
        )
        added = await svc.process_memory_updates(
            db=db, user_id=str(user.id), assistant_response=response,
        )
        assert len(added) == 1
        assert "Acme Corp" in added[0].content


# ═══════════════════════════════════════════════════════════════════════════════
# A4: Session memory cleanup
# ═══════════════════════════════════════════════════════════════════════════════


class TestSessionMemoryCleanup:
    @pytest.mark.asyncio
    async def test_cleanup_removes_expired(self, db):
        svc = MemoryService()
        user = await make_user(db)
        # Create a session memory that's already expired
        mem = await svc.update_session_memory(
            db=db, conversation_id=str(uuid.uuid4()),
            user_id=str(user.id), summary="Expired session",
        )
        # Manually expire it
        from app.models.models import SessionMemory
        from sqlalchemy import select
        stmt = select(SessionMemory).where(SessionMemory.id == mem.id)
        result = await db.execute(stmt)
        row = result.scalar_one()
        row.expires_at = datetime.utcnow() - timedelta(days=1)
        await db.commit()

        count = await svc.cleanup_expired_sessions(db)
        assert count == 1


# ═══════════════════════════════════════════════════════════════════════════════
# B1: Tool executor profile filtering
# ═══════════════════════════════════════════════════════════════════════════════


class TestToolExecutorProfileFiltering:
    @pytest.mark.asyncio
    async def test_personal_mode_blocks_graph_tools(self):
        from app.agents.tool_executor import ToolExecutor, _BLOCKED_GRAPH_TOOLS
        from app.core.mode import UserSession
        from app.schemas.auth import UserInfo

        executor = ToolExecutor()
        user = UserInfo(id="u1", email="test@test.com", name="Test")
        session = UserSession(mode="personal", user_id="u1")

        with patch.object(
            executor, "get_available_tools",
            wraps=executor.get_available_tools,
        ):
            tools = await executor.get_available_tools(user, user_session=session)
            tool_names = {t["function"]["name"] for t in tools}
            # None of the blocked graph tools should be present
            assert not tool_names.intersection(_BLOCKED_GRAPH_TOOLS)

    @pytest.mark.asyncio
    async def test_work_mode_includes_graph_tools(self):
        from app.agents.tool_executor import ToolExecutor, _BLOCKED_GRAPH_TOOLS
        from app.core.mode import UserSession
        from app.schemas.auth import UserInfo

        executor = ToolExecutor()
        user = UserInfo(id="u1", email="test@test.com", name="Test")
        session = UserSession(mode="work", user_id="u1", tenant_id="t1")

        with patch("app.core.config.settings.ENABLE_AGENTS", True):
            tools = await executor.get_available_tools(user, user_session=session)
            tool_names = {t["function"]["name"] for t in tools}
            # Graph tools should be present in work mode
            assert "get_inbox" in tool_names

    @pytest.mark.asyncio
    async def test_execute_tool_blocked_in_personal(self):
        from app.agents.tool_executor import ToolExecutor
        from app.core.mode import UserSession
        from app.schemas.auth import UserInfo

        executor = ToolExecutor()
        user = UserInfo(id="u1", email="test@test.com", name="Test")
        session = UserSession(mode="personal", user_id="u1")

        result = await executor.execute_tool(
            "get_inbox", {}, user, user_session=session,
        )
        assert "error" in result
        assert "Personal mode" in result["error"]


# ═══════════════════════════════════════════════════════════════════════════════
# E1: Private chat cleanup includes messages
# ═══════════════════════════════════════════════════════════════════════════════


class TestPrivateChatCleanup:
    @pytest.mark.asyncio
    async def test_cleanup_removes_messages_too(self):
        from app.services.private_chat_cleanup import delete_expired_private_conversations
        from app.services.chat_service import _in_memory_conversations, _in_memory_messages

        conv_id = str(uuid.uuid4())
        _in_memory_conversations[conv_id] = {
            "is_private": True,
            "private_expires_at": datetime.utcnow() - timedelta(hours=1),
        }
        _in_memory_messages[conv_id] = [{"role": "user", "content": "secret"}]

        with patch("app.core.database.db_available", False):
            count = await delete_expired_private_conversations()

        assert count == 1
        assert conv_id not in _in_memory_conversations
        assert conv_id not in _in_memory_messages


# ═══════════════════════════════════════════════════════════════════════════════
# G1: Budget notification DB dedup
# ═══════════════════════════════════════════════════════════════════════════════


class TestBudgetNotificationDedup:
    @pytest.mark.asyncio
    async def test_notification_already_sent_returns_true(self, db):
        from app.services.budget_service import _notification_already_sent

        user = await make_user(db)
        # Insert a recent notification
        notif = Notification(
            id=str(uuid.uuid4()),
            user_id=str(user.id),
            type=NotificationType.BUDGET_WARNING,
            title="Budget warning",
            message="token:70%",
            created_at=datetime.utcnow(),
        )
        db.add(notif)
        await db.flush()

        found = await _notification_already_sent(
            db, str(user.id), "warning", "token:70%",
        )
        assert found is True

    @pytest.mark.asyncio
    async def test_notification_not_sent_returns_false(self, db):
        from app.services.budget_service import _notification_already_sent

        user = await make_user(db)
        found = await _notification_already_sent(
            db, str(user.id), "warning", "token:80%",
        )
        assert found is False


# ═══════════════════════════════════════════════════════════════════════════════
# D3: Delta token persistence
# ═══════════════════════════════════════════════════════════════════════════════


class TestDeltaTokenPersistence:
    @pytest.mark.asyncio
    async def test_connector_state_model_exists(self, db):
        """ConnectorState model should be creatable in the test DB."""
        state = ConnectorState(
            id=str(uuid.uuid4()),
            connector_type="sharepoint",
            source_id="site-123",
            state_key="delta_token",
            state_value="opaque-token-value",
        )
        db.add(state)
        await db.flush()

        from sqlalchemy import select
        stmt = select(ConnectorState).where(
            ConnectorState.source_id == "site-123"
        )
        result = await db.execute(stmt)
        row = result.scalar_one()
        assert row.state_value == "opaque-token-value"
        assert row.connector_type == "sharepoint"


# ═══════════════════════════════════════════════════════════════════════════════
# Tool executor constant deduplication
# ═══════════════════════════════════════════════════════════════════════════════


class TestBlockedGraphConstant:
    def test_blocked_graph_is_frozenset(self):
        from app.agents.tool_executor import _BLOCKED_GRAPH_TOOLS
        assert isinstance(_BLOCKED_GRAPH_TOOLS, frozenset)
        assert "get_inbox" in _BLOCKED_GRAPH_TOOLS
        assert "send_email" in _BLOCKED_GRAPH_TOOLS
