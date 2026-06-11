"""Task CRUD + filters + search."""
from __future__ import annotations

import logging
from datetime import date, datetime, timedelta
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import and_, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..database import get_session
from ..deps import RequestContext, get_current_user
from ..enums import Priority, SourceType, SyncStatus, SyncTarget, TaskStatus
from ..models import Task, TaskSync
from ..schemas import TaskListResponse, TaskRead, TaskUpdate
from ..services.tasks.audit import log

logger = logging.getLogger(__name__)

router = APIRouter()


def _scope(q, ctx: RequestContext):
    return q.where(Task.tenant_id == ctx.tenant_id, Task.user_id == ctx.user_id)


@router.get("/tasks", response_model=TaskListResponse)
async def list_tasks(
    ctx: RequestContext = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
    status: Optional[TaskStatus] = None,
    priority: Optional[Priority] = None,
    source: Optional[SourceType] = None,
    needs_sync: Optional[bool] = None,
    limit: int = Query(default=100, le=500),
    offset: int = 0,
):
    q = _scope(select(Task), ctx)
    if status:
        q = q.where(Task.status == status.value)
    if priority:
        q = q.where(Task.priority == priority.value)
    if source:
        q = q.where(Task.source_type == source.value)
    q = q.order_by(Task.due_date.is_(None), Task.due_date.asc(), Task.created_at.desc())
    total = (await session.execute(_scope(select(func.count(Task.id)), ctx))).scalar_one()
    items = (await session.execute(q.offset(offset).limit(limit))).scalars().all()
    return TaskListResponse(items=[TaskRead.model_validate(t) for t in items], total=total)


@router.get("/tasks/today", response_model=TaskListResponse)
async def today(ctx: RequestContext = Depends(get_current_user),
                session: AsyncSession = Depends(get_session)):
    start = datetime.combine(date.today(), datetime.min.time())
    end = start + timedelta(days=1)
    q = _scope(select(Task), ctx).where(
        Task.due_date >= start, Task.due_date < end,
        Task.status.in_([TaskStatus.OPEN.value, TaskStatus.IN_PROGRESS.value, TaskStatus.NEEDS_REVIEW.value]),
    ).order_by(Task.priority.asc(), Task.due_date.asc())
    items = (await session.execute(q)).scalars().all()
    return TaskListResponse(items=[TaskRead.model_validate(t) for t in items], total=len(items))


@router.get("/tasks/overdue", response_model=TaskListResponse)
async def overdue(ctx: RequestContext = Depends(get_current_user),
                  session: AsyncSession = Depends(get_session)):
    now = datetime.utcnow()
    q = _scope(select(Task), ctx).where(
        Task.due_date < now,
        Task.status.in_([TaskStatus.OPEN.value, TaskStatus.IN_PROGRESS.value]),
    ).order_by(Task.due_date.asc())
    items = (await session.execute(q)).scalars().all()
    return TaskListResponse(items=[TaskRead.model_validate(t) for t in items], total=len(items))


@router.get("/tasks/search", response_model=TaskListResponse)
async def search(
    q: str = Query(..., min_length=1),
    ctx: RequestContext = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
):
    like = f"%{q}%"
    qry = _scope(select(Task), ctx).where(
        or_(Task.title.ilike(like), Task.description.ilike(like))
    ).order_by(Task.created_at.desc()).limit(100)
    items = (await session.execute(qry)).scalars().all()
    return TaskListResponse(items=[TaskRead.model_validate(t) for t in items], total=len(items))


async def _get_owned(session: AsyncSession, ctx: RequestContext, task_id: str) -> Task:
    t = await session.get(Task, task_id)
    if t is None or t.tenant_id != ctx.tenant_id or t.user_id != ctx.user_id:
        raise HTTPException(404)
    return t


@router.get("/tasks/{task_id}", response_model=TaskRead)
async def get_task(task_id: str, ctx: RequestContext = Depends(get_current_user),
                   session: AsyncSession = Depends(get_session)):
    return TaskRead.model_validate(await _get_owned(session, ctx, task_id))


@router.patch("/tasks/{task_id}", response_model=TaskRead)
async def update_task(task_id: str, payload: TaskUpdate,
                      ctx: RequestContext = Depends(get_current_user),
                      session: AsyncSession = Depends(get_session)):
    t = await _get_owned(session, ctx, task_id)
    data = payload.model_dump(exclude_unset=True)
    for k, v in data.items():
        if hasattr(t, k) and v is not None:
            setattr(t, k, v.value if hasattr(v, "value") else v)
    await log(session, tenant_id=ctx.tenant_id, user_id=ctx.user_id,
              action="task.updated", entity_type="task", entity_id=t.id, details=data)
    await session.commit()

    # If status changed to done, propagate to Planner + Excel
    new_status = data.get("status")
    if new_status in (TaskStatus.DONE.value, TaskStatus.DONE, "done"):
        await _propagate_done(session, ctx, t.id)
    return TaskRead.model_validate(t)


async def _propagate_done(session: AsyncSession, ctx: RequestContext, task_id: str) -> None:
    """When a task is marked done, mirror the change to Planner (percent=100)
    and re-sync Excel so the row reflects DONE status. Failures are logged
    but do not raise — the local DB state is the source of truth."""
    syncs = (await session.execute(
        select(TaskSync).where(
            TaskSync.task_id == task_id,
            TaskSync.tenant_id == ctx.tenant_id,
            TaskSync.user_id == ctx.user_id,
            TaskSync.sync_status == SyncStatus.SYNCED.value,
        )
    )).scalars().all()

    has_planner = any(s.target_type == SyncTarget.PLANNER.value and s.target_id for s in syncs)
    has_excel = any(s.target_type == SyncTarget.EXCEL.value for s in syncs)

    if has_planner:
        try:
            from ..services.graph.client import GraphClient
            from ..services.graph import planner as planner_graph
            client = await GraphClient.for_user(session, ctx.user_id, ctx.tenant_id)
            try:
                for s in syncs:
                    if s.target_type == SyncTarget.PLANNER.value and s.target_id:
                        await planner_graph.update_task(
                            client, s.target_id, percent_complete=100,
                        )
            finally:
                await client.aclose()
        except Exception:  # noqa: BLE001
            logger.exception("Planner mark-done propagation failed for task %s", task_id)

    # Always try to refresh Excel so the row reflects the new status, even if
    # not previously synced (auto-sync may have created it during a scan).
    try:
        from ..services.excel.sync import sync_tasks_to_excel
        await sync_tasks_to_excel(
            session, tenant_id=ctx.tenant_id, user_id=ctx.user_id,
            task_ids=[task_id],
        )
    except Exception:  # noqa: BLE001
        if has_excel:
            logger.exception("Excel mark-done propagation failed for task %s", task_id)


async def _set_status(session: AsyncSession, ctx: RequestContext, task_id: str,
                      status: TaskStatus, action: str) -> TaskRead:
    t = await _get_owned(session, ctx, task_id)
    t.status = status.value
    await log(session, tenant_id=ctx.tenant_id, user_id=ctx.user_id,
              action=action, entity_type="task", entity_id=t.id)
    await session.commit()
    if status == TaskStatus.DONE:
        await _propagate_done(session, ctx, t.id)
    return TaskRead.model_validate(t)


@router.post("/tasks/{task_id}/mark-done", response_model=TaskRead)
async def mark_done(task_id: str, ctx: RequestContext = Depends(get_current_user),
                    session: AsyncSession = Depends(get_session)):
    return await _set_status(session, ctx, task_id, TaskStatus.DONE, "task.mark_done")


@router.post("/tasks/{task_id}/ignore", response_model=TaskRead)
async def ignore(task_id: str, ctx: RequestContext = Depends(get_current_user),
                 session: AsyncSession = Depends(get_session)):
    return await _set_status(session, ctx, task_id, TaskStatus.IGNORED, "task.ignored")


@router.post("/tasks/{task_id}/mark-duplicate", response_model=TaskRead)
async def mark_duplicate(task_id: str, ctx: RequestContext = Depends(get_current_user),
                         session: AsyncSession = Depends(get_session)):
    return await _set_status(session, ctx, task_id, TaskStatus.DUPLICATE, "task.mark_duplicate")
