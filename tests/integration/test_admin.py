"""
Mela AI - Integration Tests for Admin Endpoints
"""

import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from datetime import datetime

from app.schemas.admin import UsageStats


# ---------------------------------------------------------------------------
# GET /api/v1/admin/stats
# ---------------------------------------------------------------------------

class TestAdminStatsEndpoint:
    """Tests for GET /api/v1/admin/stats."""

    @pytest.mark.asyncio
    async def test_stats_returns_usage_stats(self, admin_client, mock_db):
        """Admin users should receive aggregated usage statistics."""
        # The endpoint performs multiple db.scalar() calls. We configure
        # the mock to return deterministic counts.
        mock_db.scalar = AsyncMock(side_effect=[
            10,   # total_users
            3,    # active_users_today
            50,   # total_conversations
            200,  # total_messages
            50000,  # total_tokens
            15,   # total_documents (active)
            12,   # indexed_documents
        ])

        response = await admin_client.get("/api/v1/admin/stats")

        assert response.status_code == 200
        data = response.json()
        assert data["total_users"] == 10
        assert data["active_users_today"] == 3
        assert data["total_conversations"] == 50
        assert data["total_messages"] == 200
        assert data["total_tokens_used"] == 50000
        assert data["total_documents"] == 15
        assert data["indexed_documents"] == 12

    @pytest.mark.asyncio
    async def test_stats_returns_zeros_when_empty(self, admin_client, mock_db):
        """When the database is empty, stats should return zero values."""
        mock_db.scalar = AsyncMock(return_value=None)

        response = await admin_client.get("/api/v1/admin/stats")

        assert response.status_code == 200
        data = response.json()
        assert data["total_users"] == 0
        assert data["total_tokens_used"] == 0

    @pytest.mark.asyncio
    async def test_stats_unauthenticated(self, unauthenticated_client):
        """Unauthenticated requests should be rejected with 401."""
        response = await unauthenticated_client.get("/api/v1/admin/stats")
        assert response.status_code == 401


# ---------------------------------------------------------------------------
# GET /api/v1/admin/users
# ---------------------------------------------------------------------------

class TestAdminUsersEndpoint:
    """Tests for GET /api/v1/admin/users."""

    @pytest.mark.asyncio
    async def test_list_users_admin(self, admin_client, mock_db):
        """Admin users should be able to list all users."""
        fake_user = MagicMock()
        fake_user.id = "u-1"
        fake_user.azure_id = "az-1"
        fake_user.email = "user1@armely.com"
        fake_user.name = "User One"
        fake_user.department = "Sales"
        fake_user.job_title = "Rep"
        fake_user.role = "user"
        fake_user.preferred_model = None
        fake_user.daily_token_limit = 100000
        fake_user.tokens_used_today = 500
        fake_user.is_active = True
        fake_user.created_at = datetime(2025, 1, 15)
        fake_user.updated_at = datetime(2025, 2, 1)

        scalars_mock = MagicMock()
        scalars_mock.all.return_value = [fake_user]
        result_mock = MagicMock()
        result_mock.scalars.return_value = scalars_mock
        mock_db.execute.return_value = result_mock

        response = await admin_client.get("/api/v1/admin/users")

        assert response.status_code == 200
        data = response.json()
        assert isinstance(data, list)
        assert len(data) == 1
        assert data[0]["email"] == "user1@armely.com"

    @pytest.mark.asyncio
    async def test_list_users_empty(self, admin_client, mock_db):
        """When no users exist, an empty list should be returned."""
        scalars_mock = MagicMock()
        scalars_mock.all.return_value = []
        result_mock = MagicMock()
        result_mock.scalars.return_value = scalars_mock
        mock_db.execute.return_value = result_mock

        response = await admin_client.get("/api/v1/admin/users")

        assert response.status_code == 200
        assert response.json() == []


# ---------------------------------------------------------------------------
# Non-admin access
# ---------------------------------------------------------------------------

class TestAdminNonAdminAccess:
    """Verify that non-admin users receive 403 from admin endpoints."""

    @pytest.mark.asyncio
    async def test_stats_non_admin_gets_403(self, mock_db):
        """A regular user calling /admin/stats should get 403."""
        from fastapi import HTTPException, status
        from httpx import AsyncClient, ASGITransport
        from app.main import app
        from app.core.database import get_db
        from app.core.security import get_current_user, get_current_admin_user
        from app.schemas.auth import UserInfo

        regular_user = UserInfo(
            id="user-regular",
            email="regular@armely.com",
            name="Regular User",
            roles=["user"],
        )

        async def _override_get_db():
            yield mock_db

        async def _override_get_current_user():
            return regular_user

        async def _override_get_current_admin_user():
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Admin access required",
            )

        app.dependency_overrides[get_db] = _override_get_db
        app.dependency_overrides[get_current_user] = _override_get_current_user
        app.dependency_overrides[get_current_admin_user] = _override_get_current_admin_user

        try:
            transport = ASGITransport(app=app)
            async with AsyncClient(transport=transport, base_url="http://testserver") as ac:
                response = await ac.get("/api/v1/admin/stats")
            assert response.status_code == 403
        finally:
            app.dependency_overrides.clear()

    @pytest.mark.asyncio
    async def test_users_non_admin_gets_403(self, mock_db):
        """A regular user calling /admin/users should get 403."""
        from fastapi import HTTPException, status
        from httpx import AsyncClient, ASGITransport
        from app.main import app
        from app.core.database import get_db
        from app.core.security import get_current_user, get_current_admin_user
        from app.schemas.auth import UserInfo

        regular_user = UserInfo(
            id="user-regular",
            email="regular@armely.com",
            name="Regular User",
            roles=["user"],
        )

        async def _override_get_db():
            yield mock_db

        async def _override_get_current_user():
            return regular_user

        async def _override_get_current_admin_user():
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Admin access required",
            )

        app.dependency_overrides[get_db] = _override_get_db
        app.dependency_overrides[get_current_user] = _override_get_current_user
        app.dependency_overrides[get_current_admin_user] = _override_get_current_admin_user

        try:
            transport = ASGITransport(app=app)
            async with AsyncClient(transport=transport, base_url="http://testserver") as ac:
                response = await ac.get("/api/v1/admin/users")
            assert response.status_code == 403
        finally:
            app.dependency_overrides.clear()
