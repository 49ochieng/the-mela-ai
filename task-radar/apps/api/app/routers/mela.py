"""Mela AI tools — same logic as MCP server but exposed over HTTP for fallback.

Authentication: Mela calls this surface as the **signed-in user** via either
the browser session cookie or a per-user agent token
(``Authorization: Bearer mtr_at_<...>``). There is no shared service key and
no header-based ``X-Mela-User-Id`` impersonation path."""
from __future__ import annotations

from collections import Counter, defaultdict
from datetime import date, datetime, timedelta

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..database import get_session
from ..deps import RequestContext, get_current_user
from ..enums import ScanStatus, TaskStatus
from ..models import ScanRun, Task
from ..schemas import RunScanRequest, RunScanResponse, TaskListResponse, TaskRead
from ..services.excel.sync import sync_tasks_to_excel
from ..services.planner.sync import create_planner_task
from ..services.queue.queue import get_queue

router = APIRouter()


@router.post("/mela/tools/scan", response_model=RunScanResponse)
async def scan(payload: RunScanRequest,
               ctx: RequestContext = Depends(get_current_user),
               session: AsyncSession = Depends(get_session)):
    scope: dict = {}
    if payload.lookback_hours:
        scope["lookback_hours"] = payload.lookback_hours
    scope["include_attachments"] = bool(payload.include_attachments)
    sr = ScanRun(tenant_id=ctx.tenant_id, user_id=ctx.user_id,
                 scan_type=payload.source.value, status=ScanStatus.QUEUED.value,
                 source_scope=scope)
    session.add(sr)
    await session.commit()
    await get_queue().enqueue({"type": "scan", "scan_run_id": sr.id})
    return RunScanResponse(scan_run_id=sr.id, status=ScanStatus(sr.status))


@router.get("/mela/tools/scans/{scan_run_id}")
async def scan_status(scan_run_id: str,
                      ctx: RequestContext = Depends(get_current_user),
                      session: AsyncSession = Depends(get_session)):
    s = await session.get(ScanRun, scan_run_id)
    if not s or s.tenant_id != ctx.tenant_id or s.user_id != ctx.user_id:
        raise HTTPException(404)
    return {
        "scan_run_id": s.id,
        "status": s.status,
        "started_at": s.started_at.isoformat() if s.started_at else None,
        "completed_at": s.completed_at.isoformat() if s.completed_at else None,
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


@router.get("/mela/tools/tasks/today", response_model=TaskListResponse)
async def today(source: str | None = None,
                ctx: RequestContext = Depends(get_current_user),
                session: AsyncSession = Depends(get_session)):
    start = datetime.combine(date.today(), datetime.min.time())
    end = start + timedelta(days=1)
    q = select(Task).where(
        Task.tenant_id == ctx.tenant_id, Task.user_id == ctx.user_id,
        Task.due_date >= start, Task.due_date < end,
    )
    if source and source != "all":
        q = q.where(Task.source_type == source)
    items = (await session.execute(q)).scalars().all()
    return TaskListResponse(items=[TaskRead.model_validate(t) for t in items], total=len(items))


@router.get("/mela/tools/tasks/overdue", response_model=TaskListResponse)
async def overdue(source: str | None = None,
                  ctx: RequestContext = Depends(get_current_user),
                  session: AsyncSession = Depends(get_session)):
    q = select(Task).where(
        Task.tenant_id == ctx.tenant_id, Task.user_id == ctx.user_id,
        Task.due_date < datetime.utcnow(),
        Task.status.in_([TaskStatus.OPEN.value, TaskStatus.IN_PROGRESS.value]),
    )
    if source and source != "all":
        q = q.where(Task.source_type == source)
    items = (await session.execute(q)).scalars().all()
    return TaskListResponse(items=[TaskRead.model_validate(t) for t in items], total=len(items))


@router.post("/mela/tools/tasks/search", response_model=TaskListResponse)
async def search(query: dict,
                 ctx: RequestContext = Depends(get_current_user),
                 session: AsyncSession = Depends(get_session)):
    q = select(Task).where(Task.tenant_id == ctx.tenant_id, Task.user_id == ctx.user_id)
    if (text := query.get("query")):
        like = f"%{text}%"
        q = q.where(or_(Task.title.ilike(like), Task.description.ilike(like)))
    if query.get("source"): q = q.where(Task.source_type == query["source"])
    if query.get("status"): q = q.where(Task.status == query["status"])
    if query.get("priority"): q = q.where(Task.priority == query["priority"])
    items = (await session.execute(q.limit(200))).scalars().all()
    return TaskListResponse(items=[TaskRead.model_validate(t) for t in items], total=len(items))


@router.post("/mela/tools/tasks/{task_id}/status")
async def status(task_id: str, payload: dict,
                 ctx: RequestContext = Depends(get_current_user),
                 session: AsyncSession = Depends(get_session)):
    t = await session.get(Task, task_id)
    if not t or t.tenant_id != ctx.tenant_id or t.user_id != ctx.user_id:
        raise HTTPException(404)
    new = payload.get("status")
    valid = {s.value for s in TaskStatus}
    if new not in valid:
        raise HTTPException(400, "invalid status")
    t.status = new
    await session.commit()
    return {"task_id": t.id, "status": t.status}


@router.post("/mela/tools/tasks/{task_id}/planner")
async def to_planner(task_id: str,
                     ctx: RequestContext = Depends(get_current_user),
                     session: AsyncSession = Depends(get_session)):
    return await create_planner_task(session, tenant_id=ctx.tenant_id, user_id=ctx.user_id, task_id=task_id)


@router.post("/mela/tools/excel/sync")
async def excel_sync(payload: dict,
                     ctx: RequestContext = Depends(get_current_user),
                     session: AsyncSession = Depends(get_session)):
    return await sync_tasks_to_excel(
        session, tenant_id=ctx.tenant_id, user_id=ctx.user_id,
        task_ids=payload.get("task_ids"),
    )


# ── Mela AI conversation tools ────────────────────────────────────────
# These endpoints authenticate as the signed-in user via either the
# browser session cookie or a per-user agent token issued from the UI
# (Authorization: Bearer mtr_at_<...>). No header impersonation.
def _task_summary(t: Task) -> dict:
    return {
        "id": t.id,
        "title": t.title,
        "source": t.source_type,
        "priority": t.priority,
        "due_date": t.due_date.isoformat() if t.due_date else None,
        "status": t.status,
        "source_url": t.source_url,
    }


@router.get("/mela/tools/brief")
async def brief(
    ctx: RequestContext = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
):
    """High-level brief for the Mela AI greeting/morning summary."""
    from ..scheduler.scheduler import _next_due_local, _user_tz
    from ..models import User as _User

    user_row = await session.get(_User, ctx.user_id)
    user_tz = _user_tz(user_row.timezone if user_row else None)

    today_start = datetime.combine(date.today(), datetime.min.time())
    today_end = today_start + timedelta(days=1)
    week_end = today_start + timedelta(days=7)

    base = select(Task).where(
        Task.tenant_id == ctx.tenant_id,
        Task.user_id == ctx.user_id,
        Task.status.in_([TaskStatus.OPEN.value, TaskStatus.IN_PROGRESS.value]),
    )

    today_q = base.where(Task.due_date >= today_start, Task.due_date < today_end)
    overdue_q = base.where(Task.due_date < today_start)
    week_q = base.where(Task.due_date >= today_start, Task.due_date < week_end)

    today_tasks = (await session.execute(today_q)).scalars().all()
    overdue_tasks = (await session.execute(overdue_q)).scalars().all()
    week_tasks = (await session.execute(week_q)).scalars().all()

    by_priority: Counter[str] = Counter(t.priority or "medium" for t in week_tasks)
    by_source: Counter[str] = Counter(t.source_type or "unknown" for t in week_tasks)

    # Top 5 = overdue first (oldest), then today (highest priority first)
    pri_rank = {"high": 0, "medium": 1, "low": 2}
    overdue_sorted = sorted(overdue_tasks, key=lambda t: t.due_date or datetime.max)
    today_sorted = sorted(
        today_tasks, key=lambda t: (pri_rank.get(t.priority or "medium", 1), t.due_date or datetime.max),
    )
    top = [_task_summary(t) for t in (overdue_sorted + today_sorted)[:5]]

    return {
        "today_count": len(today_tasks),
        "overdue_count": len(overdue_tasks),
        "this_week_count": len(week_tasks),
        "by_priority": dict(by_priority),
        "by_source": dict(by_source),
        "top": top,
        "next_scan_at": _next_due_local(tz=user_tz).isoformat(),
        "generated_at": datetime.utcnow().isoformat() + "Z",
    }


@router.get("/mela/tools/tasks/this-week")
async def this_week(
    ctx: RequestContext = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
):
    """Open tasks due in the next 7 days, grouped by day, sorted by priority."""
    today_start = datetime.combine(date.today(), datetime.min.time())
    week_end = today_start + timedelta(days=7)
    q = select(Task).where(
        Task.tenant_id == ctx.tenant_id,
        Task.user_id == ctx.user_id,
        Task.status.in_([TaskStatus.OPEN.value, TaskStatus.IN_PROGRESS.value]),
        Task.due_date >= today_start,
        Task.due_date < week_end,
    )
    tasks = (await session.execute(q)).scalars().all()
    pri_rank = {"high": 0, "medium": 1, "low": 2}
    grouped: dict[str, list[dict]] = defaultdict(list)
    for t in tasks:
        day = t.due_date.date().isoformat() if t.due_date else "unscheduled"
        grouped[day].append(_task_summary(t))
    for day, items in grouped.items():
        items.sort(key=lambda d: pri_rank.get(d["priority"] or "medium", 1))
    return {"days": dict(sorted(grouped.items())), "total": len(tasks)}


@router.get("/mela/tools/top")
async def top_tasks(
    limit: int = 5,
    ctx: RequestContext = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
):
    """Top N open tasks: overdue first (oldest), then today/upcoming by priority."""
    limit = max(1, min(limit, 50))
    today_start = datetime.combine(date.today(), datetime.min.time())
    q = select(Task).where(
        Task.tenant_id == ctx.tenant_id,
        Task.user_id == ctx.user_id,
        Task.status.in_([TaskStatus.OPEN.value, TaskStatus.IN_PROGRESS.value]),
    )
    tasks = (await session.execute(q)).scalars().all()
    pri_rank = {"high": 0, "medium": 1, "low": 2}

    def _key(t: Task):
        overdue = (t.due_date or datetime.max) < today_start
        return (
            0 if overdue else 1,
            pri_rank.get(t.priority or "medium", 1),
            t.due_date or datetime.max,
        )

    tasks.sort(key=_key)
    return {"items": [_task_summary(t) for t in tasks[:limit]], "total": min(limit, len(tasks))}
