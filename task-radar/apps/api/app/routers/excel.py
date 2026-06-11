"""Excel routes."""
from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from ..database import get_session
from ..deps import RequestContext, get_current_user
from ..schemas import ExcelStatus, ExcelSyncRequest, ExcelSyncResponse
from ..services.excel.sync import get_excel_status, sync_tasks_to_excel
from ..services.graph.client import GraphClient
from ..services.graph import excel as excel_graph
from ..services.tasks.audit import log

router = APIRouter()


@router.post("/excel/create-or-update-workbook")
async def create_or_update(ctx: RequestContext = Depends(get_current_user),
                           session: AsyncSession = Depends(get_session)) -> dict:
    client = await GraphClient.for_user(session, ctx.user_id, ctx.tenant_id)
    try:
        wb = await excel_graph.find_or_create_task_workbook(client)
        await excel_graph.ensure_tasklog_table(client, wb["id"])
        url = await excel_graph.get_workbook_url(client, wb["id"])
    finally:
        await client.aclose()
    await log(session, tenant_id=ctx.tenant_id, user_id=ctx.user_id,
              action="excel.workbook_ensured", details={"url": url})
    await session.commit()
    return {"workbook_id": wb["id"], "workbook_url": url}


@router.post("/excel/sync", response_model=ExcelSyncResponse)
async def sync(payload: ExcelSyncRequest,
               ctx: RequestContext = Depends(get_current_user),
               session: AsyncSession = Depends(get_session)) -> ExcelSyncResponse:
    res = await sync_tasks_to_excel(
        session, tenant_id=ctx.tenant_id, user_id=ctx.user_id, task_ids=payload.task_ids
    )
    await log(session, tenant_id=ctx.tenant_id, user_id=ctx.user_id,
              action="excel.synced", details=res)
    await session.commit()
    return ExcelSyncResponse(**res)


@router.get("/excel/status", response_model=ExcelStatus)
async def status(ctx: RequestContext = Depends(get_current_user),
                 session: AsyncSession = Depends(get_session)) -> ExcelStatus:
    s = await get_excel_status(session, tenant_id=ctx.tenant_id, user_id=ctx.user_id)
    return ExcelStatus(**s)
