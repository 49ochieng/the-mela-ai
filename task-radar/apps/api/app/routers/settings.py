"""Settings endpoints (scan, teams, excel, planner)."""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..database import get_session
from ..deps import RequestContext, get_current_user
from ..models import ScanSettings
from ..schemas import (
    ExcelSettingsRead, ExcelSettingsUpdate,
    PlannerSettingsRead, PlannerSettingsUpdate,
    ScanSettingsRead, ScanSettingsUpdate,
    TeamsSettingsRead, TeamsSettingsUpdate,
)

router = APIRouter()


async def _get_or_create(session: AsyncSession, ctx: RequestContext) -> ScanSettings:
    res = await session.execute(
        select(ScanSettings).where(
            ScanSettings.tenant_id == ctx.tenant_id, ScanSettings.user_id == ctx.user_id
        )
    )
    s = res.scalar_one_or_none()
    if s is None:
        s = ScanSettings(tenant_id=ctx.tenant_id, user_id=ctx.user_id)
        session.add(s)
        await session.flush()
    return s


def _apply(s: ScanSettings, payload, fields: list[str]) -> None:
    data = payload.model_dump(exclude_unset=True)
    for f in fields:
        if f in data and data[f] is not None:
            setattr(s, f, data[f])


@router.get("/settings/scan", response_model=ScanSettingsRead)
async def get_scan(ctx: RequestContext = Depends(get_current_user),
                   session: AsyncSession = Depends(get_session)):
    return ScanSettingsRead.model_validate(await _get_or_create(session, ctx))


@router.patch("/settings/scan", response_model=ScanSettingsRead)
async def patch_scan(payload: ScanSettingsUpdate,
                     ctx: RequestContext = Depends(get_current_user),
                     session: AsyncSession = Depends(get_session)):
    s = await _get_or_create(session, ctx)
    _apply(s, payload, ["email_scan_enabled", "teams_scan_enabled", "daily_scan_enabled",
                         "scan_time_local", "timezone", "lookback_hours_first_scan",
                         "max_messages_per_scan", "max_ai_calls_per_scan",
                         "include_thread_context"])
    await session.commit()
    return ScanSettingsRead.model_validate(s)


@router.get("/settings/teams", response_model=TeamsSettingsRead)
async def get_teams(ctx: RequestContext = Depends(get_current_user),
                    session: AsyncSession = Depends(get_session)):
    return TeamsSettingsRead.model_validate(await _get_or_create(session, ctx))


@router.patch("/settings/teams", response_model=TeamsSettingsRead)
async def patch_teams(payload: TeamsSettingsUpdate,
                      ctx: RequestContext = Depends(get_current_user),
                      session: AsyncSession = Depends(get_session)):
    s = await _get_or_create(session, ctx)
    _apply(s, payload, ["selected_team_ids", "selected_channel_ids", "mentions_only",
                         "include_thread_context", "teams_scan_enabled"])
    await session.commit()
    return TeamsSettingsRead.model_validate(s)


@router.get("/settings/excel", response_model=ExcelSettingsRead)
async def get_excel(ctx: RequestContext = Depends(get_current_user),
                    session: AsyncSession = Depends(get_session)):
    return ExcelSettingsRead.model_validate(await _get_or_create(session, ctx))


@router.patch("/settings/excel", response_model=ExcelSettingsRead)
async def patch_excel(payload: ExcelSettingsUpdate,
                      ctx: RequestContext = Depends(get_current_user),
                      session: AsyncSession = Depends(get_session)):
    s = await _get_or_create(session, ctx)
    _apply(s, payload, ["excel_sync_enabled", "auto_archive_to_excel"])
    await session.commit()
    return ExcelSettingsRead.model_validate(s)


@router.get("/settings/planner", response_model=PlannerSettingsRead)
async def get_planner(ctx: RequestContext = Depends(get_current_user),
                      session: AsyncSession = Depends(get_session)):
    return PlannerSettingsRead.model_validate(await _get_or_create(session, ctx))


@router.patch("/settings/planner", response_model=PlannerSettingsRead)
async def patch_planner(payload: PlannerSettingsUpdate,
                        ctx: RequestContext = Depends(get_current_user),
                        session: AsyncSession = Depends(get_session)):
    s = await _get_or_create(session, ctx)
    _apply(s, payload, ["planner_sync_enabled", "planner_plan_id", "planner_bucket_id",
                         "approval_required_for_planner",
                         "auto_sync_to_planner_priority"])
    await session.commit()
    return PlannerSettingsRead.model_validate(s)
