"""Planner routes."""
from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..database import get_session
from ..deps import RequestContext, get_current_user
from ..enums import SyncStatus, SyncTarget
from ..models import TaskSync
from ..schemas import CreatePlannerTasksRequest, PlannerBucket, PlannerPlan
from ..services.graph import planner as planner_graph
from ..services.graph.client import GraphClient
from ..services.planner.sync import create_planner_task

logger = logging.getLogger(__name__)

router = APIRouter()


@router.get("/planner/plans", response_model=list[PlannerPlan])
async def plans(ctx: RequestContext = Depends(get_current_user),
                session: AsyncSession = Depends(get_session)):
    client = await GraphClient.for_user(session, ctx.user_id, ctx.tenant_id)
    try:
        return await planner_graph.list_plans(client)
    finally:
        await client.aclose()


@router.get("/planner/plans/{plan_id}/buckets", response_model=list[PlannerBucket])
async def buckets(plan_id: str,
                  ctx: RequestContext = Depends(get_current_user),
                  session: AsyncSession = Depends(get_session)):
    client = await GraphClient.for_user(session, ctx.user_id, ctx.tenant_id)
    try:
        return await planner_graph.list_buckets(client, plan_id)
    finally:
        await client.aclose()


@router.post("/tasks/{task_id}/planner")
async def create_for_task(task_id: str,
                          plan_id: str | None = None,
                          bucket_id: str | None = None,
                          ctx: RequestContext = Depends(get_current_user),
                          session: AsyncSession = Depends(get_session)):
    return await create_planner_task(
        session, tenant_id=ctx.tenant_id, user_id=ctx.user_id,
        task_id=task_id, plan_id=plan_id, bucket_id=bucket_id,
    )


@router.post("/planner/create-selected-tasks")
async def create_selected(payload: CreatePlannerTasksRequest,
                          ctx: RequestContext = Depends(get_current_user),
                          session: AsyncSession = Depends(get_session)):
    results = []
    for tid in payload.task_ids:
        try:
            r = await create_planner_task(
                session, tenant_id=ctx.tenant_id, user_id=ctx.user_id,
                task_id=tid, plan_id=payload.plan_id, bucket_id=payload.bucket_id,
            )
            results.append({"task_id": tid, **r})
        except Exception as e:
            results.append({"task_id": tid, "error": str(e)})
    return {"results": results}


@router.post("/planner/assign-existing-to-me")
async def assign_existing_to_me(
    ctx: RequestContext = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
):
    """Backfill: re-assign every previously-synced Planner task to the
    current user so they appear in My Day / My Tasks. Idempotent."""
    rows = (await session.execute(
        select(TaskSync).where(
            TaskSync.tenant_id == ctx.tenant_id,
            TaskSync.user_id == ctx.user_id,
            TaskSync.target_type == SyncTarget.PLANNER.value,
            TaskSync.sync_status == SyncStatus.SYNCED.value,
        )
    )).scalars().all()
    if not rows:
        return {"updated": 0, "skipped": 0, "failed": 0, "total": 0}
    client = await GraphClient.for_user(session, ctx.user_id, ctx.tenant_id)
    updated = skipped = failed = 0
    try:
        my_id = await planner_graph.get_my_id(client)
        if not my_id:
            raise HTTPException(502, "Could not resolve current user id from Graph")
        for s in rows:
            if not s.target_id:
                skipped += 1
                continue
            try:
                await planner_graph.update_task(client, s.target_id, assign_to_me=True)
                updated += 1
            except Exception:  # noqa: BLE001
                logger.exception("Backfill assign failed for planner task %s", s.target_id)
                failed += 1
    finally:
        await client.aclose()
    return {"updated": updated, "skipped": skipped, "failed": failed, "total": len(rows)}
