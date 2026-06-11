"""Scan endpoints — enqueue jobs, read history, surface diagnostics."""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..database import get_session
from ..deps import RequestContext, get_current_user
from ..enums import ScanStatus
from ..models import ScanEvent, ScanRun
from ..schemas import (
    RunScanRequest, RunScanResponse, ScanEventRead, ScanRunRead,
)
from ..services.queue.queue import get_queue
from ..services.tasks.audit import log

router = APIRouter()


def _build_scope(payload: RunScanRequest) -> dict:
    scope: dict = {}
    if payload.lookback_hours:
        scope["lookback_hours"] = payload.lookback_hours
    scope["include_attachments"] = bool(payload.include_attachments)
    return scope


@router.post("/scans/run", response_model=RunScanResponse)
async def run_scan(
    payload: RunScanRequest,
    ctx: RequestContext = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
):
    scan = ScanRun(
        tenant_id=ctx.tenant_id, user_id=ctx.user_id,
        scan_type=payload.source.value,
        source_scope=_build_scope(payload),
        status=ScanStatus.QUEUED.value,
    )
    session.add(scan)
    await session.flush()
    await log(
        session, tenant_id=ctx.tenant_id, user_id=ctx.user_id,
        action="scan.requested", entity_type="scan_run", entity_id=scan.id,
        details={"source": payload.source.value, "lookback_hours": payload.lookback_hours},
    )
    await session.commit()
    await get_queue().enqueue({"type": "scan", "scan_run_id": scan.id})
    return RunScanResponse(scan_run_id=scan.id, status=ScanStatus(scan.status))


@router.get("/scans", response_model=list[ScanRunRead])
async def list_scans(
    ctx: RequestContext = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
    limit: int = 50,
):
    res = await session.execute(
        select(ScanRun)
        .where(ScanRun.tenant_id == ctx.tenant_id, ScanRun.user_id == ctx.user_id)
        .order_by(ScanRun.created_at.desc()).limit(limit)
    )
    return [ScanRunRead.model_validate(s) for s in res.scalars().all()]


@router.get("/scans/{scan_run_id}", response_model=ScanRunRead)
async def get_scan(
    scan_run_id: str,
    ctx: RequestContext = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
):
    s = await session.get(ScanRun, scan_run_id)
    if s is None or s.tenant_id != ctx.tenant_id or s.user_id != ctx.user_id:
        raise HTTPException(404)
    return ScanRunRead.model_validate(s)


@router.get("/scans/{scan_run_id}/events", response_model=list[ScanEventRead])
async def get_scan_events(
    scan_run_id: str,
    ctx: RequestContext = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
    status: str | None = Query(
        default=None,
        description="success|skipped|no_task|needs_review|error",
    ),
    limit: int = Query(default=200, le=1000),
):
    s = await session.get(ScanRun, scan_run_id)
    if s is None or s.tenant_id != ctx.tenant_id or s.user_id != ctx.user_id:
        raise HTTPException(404)
    q = select(ScanEvent).where(ScanEvent.scan_run_id == scan_run_id)
    if status:
        q = q.where(ScanEvent.status == status)
    q = q.order_by(ScanEvent.created_at.asc()).limit(limit)
    rows = (await session.execute(q)).scalars().all()
    return [ScanEventRead.model_validate(r) for r in rows]


@router.post("/scans/{scan_run_id}/retry", response_model=RunScanResponse)
async def retry_scan(
    scan_run_id: str,
    ctx: RequestContext = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
):
    orig = await session.get(ScanRun, scan_run_id)
    if orig is None or orig.tenant_id != ctx.tenant_id or orig.user_id != ctx.user_id:
        raise HTTPException(404)
    new = ScanRun(
        tenant_id=ctx.tenant_id, user_id=ctx.user_id,
        scan_type=orig.scan_type, source_scope=orig.source_scope or {},
        status=ScanStatus.QUEUED.value,
    )
    session.add(new)
    await session.commit()
    await get_queue().enqueue({"type": "scan", "scan_run_id": new.id})
    return RunScanResponse(scan_run_id=new.id, status=ScanStatus(new.status))
