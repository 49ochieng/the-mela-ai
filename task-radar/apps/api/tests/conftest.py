"""Pytest fixtures: in-memory SQLite + isolated session per test."""
from __future__ import annotations

import asyncio
import os

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

# Force test config BEFORE importing the app
os.environ.setdefault("DATABASE_URL", "sqlite+aiosqlite:///:memory:")
os.environ.setdefault("FRONTEND_URL", "http://localhost:2005")
os.environ.setdefault("BACKEND_URL", "http://localhost:8012")
os.environ.setdefault("AZURE_TENANT_ID", "test-tenant")
os.environ.setdefault("AZURE_CLIENT_ID", "test-client")
os.environ.setdefault("AZURE_CLIENT_SECRET", "test-secret")
os.environ.setdefault("MICROSOFT_REDIRECT_URI", "http://localhost:8012/api/auth/microsoft/callback")
os.environ.setdefault("AZURE_OPENAI_ENDPOINT", "https://example.openai.azure.com")
os.environ.setdefault("AZURE_OPENAI_API_KEY", "test")
os.environ.setdefault("AZURE_OPENAI_DEPLOYMENT_GPT52", "gpt-5.2-chat")
os.environ.setdefault("SECRET_KEY", "x" * 48)
os.environ.setdefault("JWT_SECRET", "x" * 48)
os.environ.setdefault("TOKEN_ENCRYPTION_KEY", "")  # auto-generated for dev
os.environ.setdefault("MCP_API_KEY", "test-mcp-key")
os.environ.setdefault("QUEUE_PROVIDER", "memory")
# Tests opt into rate limiting / CSRF individually; the full suite would
# otherwise trip the auth bucket.
os.environ.setdefault("RATE_LIMIT_ENABLED", "false")
os.environ.setdefault("CSRF_ENABLED", "false")

from app.database import Base  # noqa: E402


@pytest.fixture(scope="session")
def event_loop():
    loop = asyncio.new_event_loop()
    yield loop
    loop.close()


@pytest_asyncio.fixture
async def engine():
    eng = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with eng.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield eng
    await eng.dispose()


@pytest_asyncio.fixture
async def session(engine) -> AsyncSession:
    Sm = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
    async with Sm() as s:
        yield s
