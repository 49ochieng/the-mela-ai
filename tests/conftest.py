"""
Mela AI - Shared Pytest Fixtures
"""

import sys
import os
import pytest
import pytest_asyncio
from unittest.mock import AsyncMock, MagicMock

# Ensure the backend app package is importable
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))

from httpx import AsyncClient, ASGITransport

from app.main import app
from app.core.database import get_db
from app.core.security import get_current_user, get_current_admin_user
from app.schemas.auth import UserInfo


# ---------------------------------------------------------------------------
# Mock user objects
# ---------------------------------------------------------------------------

TEST_USER = UserInfo(
    id="user-001",
    email="testuser@armely.com",
    name="Test User",
    given_name="Test",
    family_name="User",
    roles=["user"],
    department="Engineering",
    job_title="Developer",
    tenant_id="tenant-001",
)

ADMIN_USER = UserInfo(
    id="admin-001",
    email="admin@armely.com",
    name="Admin User",
    given_name="Admin",
    family_name="User",
    roles=["Admin", "user"],
    department="IT",
    job_title="Administrator",
    tenant_id="tenant-001",
)


# ---------------------------------------------------------------------------
# Database session mock
# ---------------------------------------------------------------------------

@pytest.fixture()
def mock_db():
    """Return a mock async database session.

    The mock supports the basic SQLAlchemy async session interface used by
    the endpoint handlers: execute, commit, rollback, flush, close, add,
    delete, and scalar.
    """
    session = AsyncMock()
    session.execute = AsyncMock(return_value=MagicMock())
    session.commit = AsyncMock()
    session.rollback = AsyncMock()
    session.flush = AsyncMock()
    session.close = AsyncMock()
    session.add = MagicMock()
    session.delete = AsyncMock()
    session.scalar = AsyncMock(return_value=0)
    return session


# ---------------------------------------------------------------------------
# Dependency override helpers
# ---------------------------------------------------------------------------

@pytest.fixture()
def authenticated_user():
    """Return the standard (non-admin) test user."""
    return TEST_USER


@pytest.fixture()
def admin_user():
    """Return the admin test user."""
    return ADMIN_USER


# ---------------------------------------------------------------------------
# Async HTTP test client
# ---------------------------------------------------------------------------

@pytest_asyncio.fixture()
async def client(mock_db, authenticated_user):
    """Provide an ``httpx.AsyncClient`` wired to the FastAPI app with
    dependency overrides for the database session and authentication.

    All external dependencies (Azure AD, database) are replaced with mocks
    so that integration tests exercise routing, serialization and basic
    request/response contracts without hitting real services.
    """

    async def _override_get_db():
        yield mock_db

    async def _override_get_current_user():
        return authenticated_user

    async def _override_get_current_admin_user():
        return authenticated_user

    app.dependency_overrides[get_db] = _override_get_db
    app.dependency_overrides[get_current_user] = _override_get_current_user
    app.dependency_overrides[get_current_admin_user] = _override_get_current_admin_user

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as ac:
        yield ac

    # Clean up overrides after the test
    app.dependency_overrides.clear()


@pytest_asyncio.fixture()
async def admin_client(mock_db, admin_user):
    """Provide an ``httpx.AsyncClient`` authenticated as an admin user.

    The ``get_current_admin_user`` dependency resolves to ``ADMIN_USER``
    which carries the ``Admin`` role, allowing admin-only endpoints to pass
    their authorization checks.
    """

    async def _override_get_db():
        yield mock_db

    async def _override_get_current_user():
        return admin_user

    async def _override_get_current_admin_user():
        return admin_user

    app.dependency_overrides[get_db] = _override_get_db
    app.dependency_overrides[get_current_user] = _override_get_current_user
    app.dependency_overrides[get_current_admin_user] = _override_get_current_admin_user

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as ac:
        yield ac

    app.dependency_overrides.clear()


@pytest_asyncio.fixture()
async def unauthenticated_client():
    """Provide an ``httpx.AsyncClient`` with **no** authentication overrides.

    The ``get_current_user`` dependency is overridden to raise an HTTPException
    with status 401, simulating an unauthenticated request.
    """
    from fastapi import HTTPException, status

    async def _override_get_db():
        yield AsyncMock()

    async def _override_get_current_user():
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Authentication required",
        )

    async def _override_get_current_admin_user():
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Authentication required",
        )

    app.dependency_overrides[get_db] = _override_get_db
    app.dependency_overrides[get_current_user] = _override_get_current_user
    app.dependency_overrides[get_current_admin_user] = _override_get_current_admin_user

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as ac:
        yield ac

    app.dependency_overrides.clear()
