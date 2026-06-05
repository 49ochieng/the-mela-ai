import pytest
from fastapi import HTTPException

from app.agents.tool_executor import ToolExecutor
from app.core.config import settings
from app.core.mode import (
    UserSession,
    enforce_personal_mode_boundaries,
    includes_enterprise_data_intent,
)
from app.schemas.auth import UserInfo
from app.services.search.query_pipeline import _query_hash


def _user() -> UserInfo:
    return UserInfo(
        id="u-123",
        email="user@example.com",
        name="User Example",
        roles=[],
        tenant_id="tenant-abc",
    )


def test_personal_mode_blocks_enterprise_prompts():
    s = UserSession(mode="personal", user_id="u-123", tenant_id=None)
    with pytest.raises(HTTPException) as exc:
        enforce_personal_mode_boundaries(s, "Summarize my SharePoint documents")
    assert exc.value.status_code == 403

    with pytest.raises(HTTPException):
        enforce_personal_mode_boundaries(s, "Find my company report from OneDrive")


def test_personal_mode_allows_web_queries():
    assert includes_enterprise_data_intent("What is the weather in Chicago today?") is False
    s = UserSession(mode="personal", user_id="u-123", tenant_id=None)
    # Should not raise
    enforce_personal_mode_boundaries(s, "Search the web for best CI/CD practices")


def test_work_mode_allows_enterprise_prompts():
    s = UserSession(mode="work", user_id="u-123", tenant_id="tenant-abc")
    enforce_personal_mode_boundaries(s, "Summarize my SharePoint docs")


def test_mode_safe_cache_key_changes_between_modes():
    q = "find internal report"
    h_personal = _query_hash(q, "u-123", "personal", "u-123", None)
    h_work = _query_hash(q, "tenant-abc", "org", "u-123", None)
    assert h_personal != h_work


@pytest.mark.asyncio
async def test_personal_mode_strips_enterprise_tools(monkeypatch):
    monkeypatch.setattr(settings, "ENABLE_AGENTS", True, raising=False)
    te = ToolExecutor()
    tools = await te.get_available_tools(
        _user(),
        user_session=UserSession(mode="personal", user_id="u-123"),
    )
    names = {t.get("function", {}).get("name") for t in tools}
    assert "get_inbox" not in names
    assert "create_draft_email" not in names
    assert "search_documents" in names


@pytest.mark.asyncio
async def test_personal_mode_blocks_graph_execution(monkeypatch):
    monkeypatch.setattr(settings, "ENABLE_AGENTS", True, raising=False)
    te = ToolExecutor()
    result = await te.execute_tool(
        "get_inbox",
        {"limit": 5},
        _user(),
        user_session=UserSession(mode="personal", user_id="u-123"),
    )
    assert "error" in result
    assert "Personal mode" in result["error"]


@pytest.mark.asyncio
async def test_switch_work_to_personal_blocks_enterprise_immediately(monkeypatch):
    monkeypatch.setattr(settings, "ENABLE_AGENTS", True, raising=False)
    te = ToolExecutor()
    work = await te.get_available_tools(
        _user(),
        user_session=UserSession(mode="work", user_id="u-123", tenant_id="tenant-abc"),
    )
    assert "get_inbox" in {t.get("function", {}).get("name") for t in work}

    personal_result = await te.execute_tool(
        "get_inbox",
        {"limit": 5},
        _user(),
        user_session=UserSession(mode="personal", user_id="u-123"),
    )
    assert "error" in personal_result
