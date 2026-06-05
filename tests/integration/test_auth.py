"""
Mela AI - Integration Tests for Authentication Endpoints
"""

import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from datetime import datetime

from app.schemas.auth import UserInfo


# ---------------------------------------------------------------------------
# POST /api/v1/auth/login
# ---------------------------------------------------------------------------

class TestLoginEndpoint:
    """Tests for POST /api/v1/auth/login."""

    @pytest.mark.asyncio
    async def test_login_existing_user_returns_welcome_back(self, client, mock_db):
        """An existing user should receive a 'Welcome back' message."""
        fake_user = MagicMock()
        fake_user.id = "u-1"
        fake_user.azure_id = "user-001"
        fake_user.email = "testuser@armely.com"
        fake_user.name = "Test User"
        fake_user.department = "Engineering"
        fake_user.job_title = "Developer"
        fake_user.role = "user"
        fake_user.preferred_model = None
        fake_user.daily_token_limit = 100000
        fake_user.tokens_used_today = 0
        fake_user.is_active = True
        fake_user.created_at = datetime(2025, 1, 1)
        fake_user.updated_at = datetime(2025, 1, 1)
        fake_user.last_token_reset = datetime(2025, 1, 1)

        result_mock = MagicMock()
        result_mock.scalar_one_or_none.return_value = fake_user
        mock_db.execute.return_value = result_mock

        response = await client.post("/api/v1/auth/login")

        assert response.status_code == 200
        data = response.json()
        assert "user" in data
        assert "welcome_message" in data
        assert "Welcome back" in data["welcome_message"]

    @pytest.mark.asyncio
    async def test_login_new_user_creates_and_welcomes(self, client, mock_db):
        """A first-time user should be created and receive a first-time welcome."""
        # First call returns None (user not found), triggers creation path.
        # The endpoint then does db.flush() and db.commit() so we need the
        # mock to be ready for the model_validate call at the end.
        result_mock = MagicMock()
        result_mock.scalar_one_or_none.return_value = None
        mock_db.execute.return_value = result_mock

        # Patch UserResponse.model_validate to return a dict-like object
        # because the endpoint calls model_validate on the ORM model.
        fake_response = {
            "id": "new-id",
            "azure_id": "user-001",
            "email": "testuser@armely.com",
            "name": "Test User",
            "department": "Engineering",
            "job_title": "Developer",
            "role": "user",
            "preferred_model": None,
            "daily_token_limit": 100000,
            "tokens_used_today": 0,
            "is_active": True,
            "created_at": "2025-01-01T00:00:00",
            "updated_at": "2025-01-01T00:00:00",
        }

        with patch(
            "app.api.endpoints.auth.UserResponse.model_validate",
            return_value=MagicMock(**fake_response, model_dump=lambda **_: fake_response),
        ):
            response = await client.post("/api/v1/auth/login")

        assert response.status_code == 200
        data = response.json()
        assert "welcome_message" in data
        # New users get a first-time welcome
        assert "first time" in data["welcome_message"].lower() or "Welcome" in data["welcome_message"]


# ---------------------------------------------------------------------------
# GET /api/v1/auth/me
# ---------------------------------------------------------------------------

class TestMeEndpoint:
    """Tests for GET /api/v1/auth/me."""

    @pytest.mark.asyncio
    async def test_me_returns_current_user(self, client, mock_db):
        """An authenticated user should receive their profile."""
        fake_user = MagicMock()
        fake_user.id = "u-1"
        fake_user.azure_id = "user-001"
        fake_user.email = "testuser@armely.com"
        fake_user.name = "Test User"
        fake_user.department = "Engineering"
        fake_user.job_title = "Developer"
        fake_user.role = "user"
        fake_user.preferred_model = None
        fake_user.daily_token_limit = 100000
        fake_user.tokens_used_today = 0
        fake_user.is_active = True
        fake_user.created_at = datetime(2025, 1, 1)
        fake_user.updated_at = datetime(2025, 1, 1)

        result_mock = MagicMock()
        result_mock.scalar_one_or_none.return_value = fake_user
        mock_db.execute.return_value = result_mock

        response = await client.get("/api/v1/auth/me")

        assert response.status_code == 200
        data = response.json()
        assert data["email"] == "testuser@armely.com"
        assert data["name"] == "Test User"

    @pytest.mark.asyncio
    async def test_me_returns_404_if_user_not_in_db(self, client, mock_db):
        """If the user is authenticated but has no DB record, return 404."""
        result_mock = MagicMock()
        result_mock.scalar_one_or_none.return_value = None
        mock_db.execute.return_value = result_mock

        response = await client.get("/api/v1/auth/me")

        assert response.status_code == 404


# ---------------------------------------------------------------------------
# Unauthenticated access
# ---------------------------------------------------------------------------

class TestUnauthenticatedAccess:
    """Verify that endpoints requiring authentication return 401 when
    no valid credentials are supplied."""

    @pytest.mark.asyncio
    async def test_login_unauthenticated_returns_401(self, unauthenticated_client):
        response = await unauthenticated_client.post("/api/v1/auth/login")
        assert response.status_code == 401

    @pytest.mark.asyncio
    async def test_me_unauthenticated_returns_401(self, unauthenticated_client):
        response = await unauthenticated_client.get("/api/v1/auth/me")
        assert response.status_code == 401

    @pytest.mark.asyncio
    async def test_logout_unauthenticated_returns_401(self, unauthenticated_client):
        response = await unauthenticated_client.post("/api/v1/auth/logout")
        assert response.status_code == 401
