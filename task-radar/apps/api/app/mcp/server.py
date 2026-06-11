"""MCP server exposing Task Radar tools.

Implements both:
- HTTP JSON-RPC-ish endpoint (POST /mcp/call) authenticated by per-user
  agent tokens (``Authorization: Bearer mtr_at_...``). The legacy shared
  ``X-Api-Key`` flow has been removed — every call is attributed to a real
  signed-in user, scoped to that user's tenant, and ``user_id`` is forced
  onto every tool invocation so impersonation across users is impossible.
- stdio MCP protocol via the `mcp` package (when invoked as ``python -m
  app.mcp.server stdio``).

For MVP we ship the HTTP variant by default — it's the most operationally
simple way for Mela AI to call Task Radar from a hosted environment. The
stdio variant is a thin wrapper that delegates to the same tool functions.
"""
from __future__ import annotations

import asyncio
import logging
import sys
from datetime import date, datetime, timedelta
from typing import Any

import uvicorn
from fastapi import Depends, FastAPI, HTTPException
from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..config import get_settings  # noqa: F401  (kept for stdio entry point)
from ..database import get_session, session_scope
from ..enums import ScanStatus, ScanType, TaskStatus
from ..logging_config import setup_logging
from ..models import ScanRun, Task, User
from ..services.excel.sync import sync_tasks_to_excel
from ..services.planner.sync import create_planner_task
from ..services.queue.queue import get_queue

logger = logging.getLogger(__name__)


# ── tool implementations (db-aware) ──────────────────────────
async def _resolve_user(session: AsyncSession, user_id: str | None) -> User:
    """Resolve target user. The caller MUST pass an explicit user_id.

    The previous "first user in the DB" fallback was removed for safety: it
    silently impersonated whichever user happened to be first in the table when
    Mela AI omitted the field, which is unacceptable for a multi-user product.
    """
    if not user_id:
        raise ValueError(
            "user_id is required. Pass it in arguments "
            "(e.g. {\"user_id\": \"<uuid>\", ...})."
        )
    u = await session.get(User, user_id)
    if not u:
        raise ValueError("user not found")
    return u


async def tool_scan_for_tasks(args: dict[str, Any]) -> dict[str, Any]:
    source = ScanType(args.get("source", "all"))
    lookback = args.get("lookback_hours")
    user_id = args.get("user_id")
    async with session_scope() as session:
        u = await _resolve_user(session, user_id)
        sr = ScanRun(
            tenant_id=u.tenant_id, user_id=u.id, scan_type=source.value,
            source_scope={"lookback_hours": lookback} if lookback else {},
            status=ScanStatus.PENDING.value,
        )
        session.add(sr)
        await session.flush()
        await get_queue().enqueue({"type": "scan", "scan_run_id": sr.id})
        return {"scan_run_id": sr.id, "status": sr.status, "summary": "queued"}


async def tool_get_today_tasks(args: dict[str, Any]) -> dict[str, Any]:
    user_id = args.get("user_id")
    source = args.get("source")
    start = datetime.combine(date.today(), datetime.min.time())
    end = start + timedelta(days=1)
    async with session_scope() as session:
        u = await _resolve_user(session, user_id)
        q = select(Task).where(
            Task.tenant_id == u.tenant_id, Task.user_id == u.id,
            Task.due_date >= start, Task.due_date < end,
        )
        if source and source != "all":
            q = q.where(Task.source_type == source)
        rows = (await session.execute(q)).scalars().all()
    grouped: dict[str, list] = {"high": [], "medium": [], "low": []}
    for t in rows:
        grouped.setdefault(t.priority, []).append({
            "id": t.id, "title": t.title, "due_date": t.due_date.isoformat() if t.due_date else None,
            "source_link": t.source_link, "status": t.status, "source": t.source_type,
        })
    return {"tasks_by_priority": grouped, "total": len(rows)}


async def tool_get_overdue_tasks(args: dict[str, Any]) -> dict[str, Any]:
    user_id = args.get("user_id")
    source = args.get("source")
    async with session_scope() as session:
        u = await _resolve_user(session, user_id)
        q = select(Task).where(
            Task.tenant_id == u.tenant_id, Task.user_id == u.id,
            Task.due_date < datetime.utcnow(),
            Task.status.in_([TaskStatus.OPEN.value, TaskStatus.IN_PROGRESS.value]),
        ).order_by(Task.due_date.asc())
        if source and source != "all":
            q = q.where(Task.source_type == source)
        rows = (await session.execute(q)).scalars().all()
    return {
        "tasks": [
            {"id": t.id, "title": t.title, "priority": t.priority,
             "due_date": t.due_date.isoformat() if t.due_date else None,
             "source_link": t.source_link, "source": t.source_type}
            for t in rows
        ]
    }


async def tool_search_tasks(args: dict[str, Any]) -> dict[str, Any]:
    user_id = args.get("user_id")
    async with session_scope() as session:
        u = await _resolve_user(session, user_id)
        q = select(Task).where(Task.tenant_id == u.tenant_id, Task.user_id == u.id)
        if (text := args.get("query")):
            like = f"%{text}%"
            q = q.where(or_(Task.title.ilike(like), Task.description.ilike(like)))
        if args.get("source") and args["source"] != "all":
            q = q.where(Task.source_type == args["source"])
        if args.get("status"): q = q.where(Task.status == args["status"])
        if args.get("priority"): q = q.where(Task.priority == args["priority"])
        # Optional date_range: {"from": "YYYY-MM-DD", "to": "YYYY-MM-DD"}
        dr = args.get("date_range") or {}
        if dr.get("from"):
            q = q.where(Task.due_date >= datetime.fromisoformat(dr["from"]))
        if dr.get("to"):
            q = q.where(Task.due_date <= datetime.fromisoformat(dr["to"]))
        rows = (await session.execute(q.limit(200))).scalars().all()
    return {"tasks": [{"id": t.id, "title": t.title, "priority": t.priority,
                        "status": t.status, "source_link": t.source_link,
                        "source": t.source_type} for t in rows]}


async def tool_update_task_status(args: dict[str, Any]) -> dict[str, Any]:
    user_id = args.get("user_id")
    new_status = args["status"]
    async with session_scope() as session:
        u = await _resolve_user(session, user_id)
        t = await session.get(Task, args["task_id"])
        if not t or t.tenant_id != u.tenant_id or t.user_id != u.id:
            raise ValueError("task not found")
        t.status = new_status
        return {"task": {"id": t.id, "status": t.status}}


async def tool_sync_tasks_to_excel(args: dict[str, Any]) -> dict[str, Any]:
    user_id = args.get("user_id")
    async with session_scope() as session:
        u = await _resolve_user(session, user_id)
        return await sync_tasks_to_excel(
            session, tenant_id=u.tenant_id, user_id=u.id, task_ids=args.get("task_ids")
        )


async def tool_create_planner_task(args: dict[str, Any]) -> dict[str, Any]:
    user_id = args.get("user_id")
    async with session_scope() as session:
        u = await _resolve_user(session, user_id)
        return await create_planner_task(
            session, tenant_id=u.tenant_id, user_id=u.id,
            task_id=args["task_id"], plan_id=args.get("plan_id"), bucket_id=args.get("bucket_id"),
        )


async def tool_get_task_brief(args: dict[str, Any]) -> dict[str, Any]:
    today = await tool_get_today_tasks(args)
    overdue = await tool_get_overdue_tasks(args)
    high = today["tasks_by_priority"].get("high", [])
    src = args.get("source")
    src_label = f" from {src}" if src and src != "all" else ""
    summary = (
        f"You have {today['total']} tasks{src_label} due today, "
        f"{len(overdue['tasks'])} overdue, and {len(high)} high-priority items."
    )
    return {"summary": summary, "counts": {"today": today["total"], "overdue": len(overdue["tasks"]),
                                              "high_today": len(high)},
            "top_items": high[:5] + overdue["tasks"][:5]}


async def tool_get_scan_status(args: dict[str, Any]) -> dict[str, Any]:
    user_id = args.get("user_id")
    async with session_scope() as session:
        u = await _resolve_user(session, user_id)
        s = await session.get(ScanRun, args["scan_run_id"])
        # Hard ownership check — never leak another user's scan, even within the
        # same tenant. Both checks must pass.
        if (
            not s
            or s.user_id != u.id
            or s.tenant_id != u.tenant_id
        ):
            raise ValueError("scan not found")
        return {
            "status": s.status,
            "metrics": {
                "messages_scanned": s.messages_scanned,
                "messages_skipped": s.messages_skipped,
                "noise_skipped": s.noise_skipped_count,
                "duplicates_skipped": s.duplicate_skipped_count,
                "ai_attempted": s.ai_attempted_count,
                "ai_success": s.ai_success_count,
                "ai_no_task": s.ai_no_task_count,
                "ai_failed": s.ai_failed_count,
                "needs_review": s.needs_review_count,
                "attachment_failed": s.attachment_failed_count,
                "excel_failed": s.excel_failed_count,
                "planner_failed": s.planner_failed_count,
                "tasks_found": s.tasks_found,
                "tasks_created": s.tasks_created,
                "tasks_deduped": s.tasks_deduped,
                "errors_count": s.errors_count,
            },
            "error_categories": s.error_categories_json or {},
            "errors": s.error_summary,
        }


async def tool_get_this_week_tasks(args: dict[str, Any]) -> dict[str, Any]:
    user_id = args.get("user_id")
    today_start = datetime.combine(date.today(), datetime.min.time())
    week_end = today_start + timedelta(days=7)
    async with session_scope() as session:
        u = await _resolve_user(session, user_id)
        q = select(Task).where(
            Task.tenant_id == u.tenant_id, Task.user_id == u.id,
            Task.status.in_([TaskStatus.OPEN.value, TaskStatus.IN_PROGRESS.value]),
            Task.due_date >= today_start, Task.due_date < week_end,
        )
        rows = (await session.execute(q)).scalars().all()
    pri_rank = {"high": 0, "medium": 1, "low": 2}
    days: dict[str, list] = {}
    for t in rows:
        day = t.due_date.date().isoformat() if t.due_date else "unscheduled"
        days.setdefault(day, []).append({
            "id": t.id, "title": t.title, "priority": t.priority,
            "due_date": t.due_date.isoformat() if t.due_date else None,
            "source_link": t.source_link, "source": t.source_type,
        })
    for items in days.values():
        items.sort(key=lambda d: pri_rank.get(d["priority"] or "medium", 1))
    return {"days": dict(sorted(days.items())), "total": len(rows)}


async def tool_get_top_tasks(args: dict[str, Any]) -> dict[str, Any]:
    user_id = args.get("user_id")
    limit = max(1, min(int(args.get("limit", 5)), 50))
    today_start = datetime.combine(date.today(), datetime.min.time())
    async with session_scope() as session:
        u = await _resolve_user(session, user_id)
        q = select(Task).where(
            Task.tenant_id == u.tenant_id, Task.user_id == u.id,
            Task.status.in_([TaskStatus.OPEN.value, TaskStatus.IN_PROGRESS.value]),
        )
        rows = (await session.execute(q)).scalars().all()
    pri_rank = {"high": 0, "medium": 1, "low": 2}

    def _key(t: Task):
        overdue = (t.due_date or datetime.max) < today_start
        return (
            0 if overdue else 1,
            pri_rank.get(t.priority or "medium", 1),
            t.due_date or datetime.max,
        )

    rows.sort(key=_key)
    return {
        "items": [
            {"id": t.id, "title": t.title, "priority": t.priority,
             "due_date": t.due_date.isoformat() if t.due_date else None,
             "source_link": t.source_link, "source": t.source_type, "status": t.status}
            for t in rows[:limit]
        ],
        "total": min(limit, len(rows)),
    }


TOOLS: dict[str, Any] = {
    "scan_for_tasks": tool_scan_for_tasks,
    "get_today_tasks": tool_get_today_tasks,
    "get_overdue_tasks": tool_get_overdue_tasks,
    "search_tasks": tool_search_tasks,
    "update_task_status": tool_update_task_status,
    "sync_tasks_to_excel": tool_sync_tasks_to_excel,
    "create_planner_task": tool_create_planner_task,
    "get_task_brief": tool_get_task_brief,
    "get_scan_status": tool_get_scan_status,
    "get_this_week_tasks": tool_get_this_week_tasks,
    "get_top_tasks": tool_get_top_tasks,
}


# ── HTTP server ──────────────────────────────────────────────
def create_http_app() -> FastAPI:
    """MCP HTTP transport.

    Authentication: the caller MUST present either a browser session cookie
    (``mtr_session``) **or** a per-user agent token
    (``Authorization: Bearer mtr_at_<...>``). Both resolve to a single
    ``RequestContext`` whose ``user_id`` is then injected into every tool
    call \u2014 callers cannot pass ``user_id`` themselves, so impersonation is
    impossible.
    """
    from ..deps import get_current_user, RequestContext  # local import to avoid cycle

    app = FastAPI(title="Mela Task Radar MCP")

    @app.get("/mcp/tools")
    async def list_tools(ctx: RequestContext = Depends(get_current_user)) -> dict:
        return {"tools": list(TOOLS.keys())}

    @app.post("/mcp/call")
    async def call(
        payload: dict,
        ctx: RequestContext = Depends(get_current_user),
    ) -> dict:
        # Accept either {"tool": ...} or {"name": ...} for caller convenience.
        name = payload.get("tool") or payload.get("name")
        # Force the calling identity onto every tool invocation. Any user_id
        # the caller may have supplied in arguments is ignored.
        args = dict(payload.get("arguments") or {})
        args["user_id"] = ctx.user_id
        if name not in TOOLS:
            raise HTTPException(404, f"Unknown tool {name}")
        logger.info(
            "mcp.tool.invoke tool=%s user_id=%s auth=%s arg_keys=%s",
            name, ctx.user_id, ctx.auth_method, sorted(args.keys()),
        )
        try:
            result = await TOOLS[name](args)
            logger.info("mcp.tool.ok tool=%s user_id=%s", name, ctx.user_id)
            return {"ok": True, "result": result}
        except ValueError as e:
            logger.warning(
                "mcp.tool.bad_request tool=%s user_id=%s err=%s",
                name, ctx.user_id, e,
            )
            raise HTTPException(400, str(e))
        except Exception as e:
            logger.exception("mcp.tool.error tool=%s", name)
            raise HTTPException(500, str(e))

    @app.get("/health")
    async def health() -> dict:
        return {"status": "ok"}

    return app


# ── stdio MCP variant (best-effort using `mcp` package) ──────
async def _run_stdio() -> None:
    try:
        from mcp.server import Server
        from mcp.server.stdio import stdio_server
        from mcp.types import TextContent, Tool
    except Exception as e:
        logger.warning("mcp package not available, stdio mode disabled: %s", e)
        return

    server = Server("mela-task-radar")

    @server.list_tools()
    async def _list() -> list[Tool]:
        return [Tool(name=n, description=f"Mela Task Radar tool: {n}", inputSchema={"type": "object"})
                for n in TOOLS]

    @server.call_tool()
    async def _call(name: str, arguments: dict) -> list[TextContent]:
        result = await TOOLS[name](arguments or {})
        import json
        return [TextContent(type="text", text=json.dumps(result, default=str))]

    async with stdio_server() as (r, w):
        await server.run(r, w, server.create_initialization_options())


def main() -> None:
    setup_logging()
    if len(sys.argv) > 1 and sys.argv[1] == "stdio":
        asyncio.run(_run_stdio())
        return
    uvicorn.run(create_http_app(), host="0.0.0.0", port=8090)


if __name__ == "__main__":
    main()
