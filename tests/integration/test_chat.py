"""
Mela AI - Integration Tests for Chat Endpoints
"""

import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from datetime import datetime


# ---------------------------------------------------------------------------
# GET /api/v1/chat/models
# ---------------------------------------------------------------------------

class TestModelsEndpoint:
    """Tests for GET /api/v1/chat/models."""

    @pytest.mark.asyncio
    async def test_list_models_returns_list(self, client):
        """The models endpoint should return a non-empty list of model info."""
        response = await client.get("/api/v1/chat/models")

        assert response.status_code == 200
        data = response.json()
        assert isinstance(data, list)
        assert len(data) >= 1

    @pytest.mark.asyncio
    async def test_list_models_contains_gpt4o(self, client):
        """GPT-4o should be present as the default model."""
        response = await client.get("/api/v1/chat/models")

        data = response.json()
        ids = [m["id"] for m in data]
        assert "gpt-4o" in ids

    @pytest.mark.asyncio
    async def test_list_models_structure(self, client):
        """Each model entry should have the expected keys."""
        response = await client.get("/api/v1/chat/models")

        data = response.json()
        expected_keys = {"id", "name", "description", "max_tokens",
                         "supports_vision", "supports_tools", "is_default"}
        for model in data:
            assert expected_keys.issubset(set(model.keys()))

    @pytest.mark.asyncio
    async def test_list_models_has_one_default(self, client):
        """Exactly one model should be marked as default."""
        response = await client.get("/api/v1/chat/models")

        data = response.json()
        defaults = [m for m in data if m["is_default"]]
        assert len(defaults) == 1

    @pytest.mark.asyncio
    async def test_list_models_unauthenticated(self, unauthenticated_client):
        """Unauthenticated requests to /models should return 401."""
        response = await unauthenticated_client.get("/api/v1/chat/models")
        assert response.status_code == 401


# ---------------------------------------------------------------------------
# POST /api/v1/chat/conversations
# ---------------------------------------------------------------------------

class TestCreateConversation:
    """Tests for POST /api/v1/chat/conversations."""

    @pytest.mark.asyncio
    async def test_create_conversation_success(self, client, mock_db):
        """Creating a conversation should return the new conversation data."""
        fake_conv = MagicMock()
        fake_conv.id = "conv-new"
        fake_conv.title = "New Conversation"
        fake_conv.model = "gpt-4o"
        fake_conv.system_prompt = None
        fake_conv.is_archived = False
        fake_conv.created_at = datetime(2025, 6, 1)
        fake_conv.updated_at = datetime(2025, 6, 1)

        with patch(
            "app.api.endpoints.chat.chat_service.get_or_create_conversation",
            new_callable=AsyncMock,
            return_value=fake_conv,
        ):
            response = await client.post(
                "/api/v1/chat/conversations",
                json={"title": "New Conversation", "model": "gpt-4o"},
            )

        assert response.status_code == 200
        data = response.json()
        assert data["id"] == "conv-new"
        assert data["title"] == "New Conversation"
        assert data["model"] == "gpt-4o"
        assert data["is_archived"] is False
        assert data["message_count"] == 0

    @pytest.mark.asyncio
    async def test_create_conversation_default_title(self, client, mock_db):
        """Omitting the title should result in the default value."""
        fake_conv = MagicMock()
        fake_conv.id = "conv-def"
        fake_conv.title = "New Conversation"
        fake_conv.model = "gpt-4o"
        fake_conv.system_prompt = None
        fake_conv.is_archived = False
        fake_conv.created_at = datetime(2025, 6, 1)
        fake_conv.updated_at = datetime(2025, 6, 1)

        with patch(
            "app.api.endpoints.chat.chat_service.get_or_create_conversation",
            new_callable=AsyncMock,
            return_value=fake_conv,
        ):
            response = await client.post(
                "/api/v1/chat/conversations",
                json={},
            )

        assert response.status_code == 200
        data = response.json()
        assert data["title"] == "New Conversation"


# ---------------------------------------------------------------------------
# GET /api/v1/chat/conversations
# ---------------------------------------------------------------------------

class TestListConversations:
    """Tests for GET /api/v1/chat/conversations."""

    @pytest.mark.asyncio
    async def test_list_conversations_empty(self, client, mock_db):
        """When no conversations exist, an empty list is returned."""
        with patch(
            "app.api.endpoints.chat.chat_service.list_conversations",
            new_callable=AsyncMock,
            return_value=[],
        ):
            response = await client.get("/api/v1/chat/conversations")

        assert response.status_code == 200
        assert response.json() == []

    @pytest.mark.asyncio
    async def test_list_conversations_returns_items(self, client, mock_db):
        """Should return a list of conversation response objects."""
        from app.schemas.chat import ConversationResponse

        fake_conversations = [
            ConversationResponse(
                id="c-1",
                title="First Chat",
                model="gpt-4o",
                system_prompt=None,
                is_archived=False,
                message_count=5,
                created_at=datetime(2025, 5, 1),
                updated_at=datetime(2025, 5, 2),
            ),
            ConversationResponse(
                id="c-2",
                title="Second Chat",
                model="gpt-4o-mini",
                system_prompt=None,
                is_archived=False,
                message_count=2,
                created_at=datetime(2025, 5, 3),
                updated_at=datetime(2025, 5, 4),
            ),
        ]

        with patch(
            "app.api.endpoints.chat.chat_service.list_conversations",
            new_callable=AsyncMock,
            return_value=fake_conversations,
        ):
            response = await client.get("/api/v1/chat/conversations")

        assert response.status_code == 200
        data = response.json()
        assert len(data) == 2
        assert data[0]["id"] == "c-1"
        assert data[1]["id"] == "c-2"

    @pytest.mark.asyncio
    async def test_list_conversations_unauthenticated(self, unauthenticated_client):
        response = await unauthenticated_client.get("/api/v1/chat/conversations")
        assert response.status_code == 401


# ---------------------------------------------------------------------------
# DELETE /api/v1/chat/conversations/{conversation_id}
# ---------------------------------------------------------------------------

class TestDeleteConversation:
    """Tests for DELETE /api/v1/chat/conversations/{conversation_id}."""

    @pytest.mark.asyncio
    async def test_delete_conversation_success(self, client, mock_db):
        """Deleting an existing conversation should return a success message."""
        with patch(
            "app.api.endpoints.chat.chat_service.delete_conversation",
            new_callable=AsyncMock,
            return_value=True,
        ):
            response = await client.delete("/api/v1/chat/conversations/conv-1")

        assert response.status_code == 200
        data = response.json()
        assert data["message"] == "Conversation deleted"

    @pytest.mark.asyncio
    async def test_delete_conversation_not_found(self, client, mock_db):
        """Attempting to delete a non-existent conversation should return 404."""
        with patch(
            "app.api.endpoints.chat.chat_service.delete_conversation",
            new_callable=AsyncMock,
            return_value=False,
        ):
            response = await client.delete("/api/v1/chat/conversations/does-not-exist")

        assert response.status_code == 404

    @pytest.mark.asyncio
    async def test_delete_conversation_unauthenticated(self, unauthenticated_client):
        response = await unauthenticated_client.delete(
            "/api/v1/chat/conversations/conv-1"
        )
        assert response.status_code == 401
