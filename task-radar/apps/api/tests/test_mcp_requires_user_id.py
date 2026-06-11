"""MCP tools must always require an explicit user_id; the previous
'first user in the database' fallback is gone."""
from __future__ import annotations

import pytest

from app.mcp.server import _resolve_user, tool_get_today_tasks


@pytest.mark.asyncio
async def test_resolve_user_requires_user_id(session):
    with pytest.raises(ValueError, match="user_id is required"):
        await _resolve_user(session, None)


@pytest.mark.asyncio
async def test_tool_without_user_id_raises():
    with pytest.raises(ValueError, match="user_id is required"):
        await tool_get_today_tasks({})


@pytest.mark.asyncio
async def test_tool_with_unknown_user_id_raises(session):
    with pytest.raises(ValueError, match="user not found"):
        await _resolve_user(session, "00000000-0000-0000-0000-000000000000")
