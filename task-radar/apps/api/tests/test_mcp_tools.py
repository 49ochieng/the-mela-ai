import pytest
from app.mcp.server import TOOLS


def test_all_required_mcp_tools_registered():
    expected = {
        "scan_for_tasks", "get_today_tasks", "get_overdue_tasks", "search_tasks",
        "update_task_status", "sync_tasks_to_excel", "create_planner_task",
        "get_task_brief", "get_scan_status",
    }
    assert expected.issubset(TOOLS.keys())


@pytest.mark.asyncio
async def test_get_scan_status_missing_raises():
    from app.mcp.server import tool_get_scan_status
    with pytest.raises(Exception):
        await tool_get_scan_status({"scan_run_id": "does-not-exist"})
