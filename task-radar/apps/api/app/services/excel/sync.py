"""Excel sync orchestration — idempotent multi-sheet upsert."""
from __future__ import annotations

import logging
from datetime import datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from ...enums import SyncStatus, SyncTarget, TaskStatus
from ...models import Task, TaskSync
from ..graph import excel as excel_graph
from ..graph.client import GraphClient
from ..tasks.priority import urgency_bucket

logger = logging.getLogger(__name__)


def _row_for_task(t: Task) -> list[Any]:
    sm = t.source_message
    received = sm.received_at.isoformat() if sm and sm.received_at else ""
    sender = ""
    subject = ""
    if sm:
        sender = sm.sender_email or sm.sender_name or ""
        subject = sm.subject_or_channel or ""
    description = (t.description or t.title or "")
    if len(description) > 280:
        description = description[:277] + "..."
    due_date = t.due_date.date().isoformat() if t.due_date else ""
    due_time = t.due_date.strftime("%H:%M") if t.due_date else ""
    bucket = urgency_bucket(t.due_date)
    planner_link = ""
    attachment_links = ""
    for s in (t.syncs or []):
        if s.target_type == SyncTarget.PLANNER.value and s.target_url:
            planner_link = s.target_url
    if t.attachments:
        attachment_links = " | ".join(
            (a.archive_url or a.source_url or a.file_name) for a in t.attachments
        )
    return [
        t.id,
        t.source_type,
        received,
        sender,
        subject,
        t.title,
        description,
        t.task_type,
        due_date,
        due_time,
        t.priority,
        int(getattr(t, "priority_score", 0) or 0),
        bucket,
        round(float(t.confidence or 0.0), 3),
        t.status,
        (t.priority_reasoning or "")[:1000],
        (t.evidence or "")[:500],
        attachment_links,
        t.source_link or "",
        planner_link,
        datetime.utcnow().isoformat(),
    ]


async def sync_tasks_to_excel(
    session: AsyncSession,
    *,
    tenant_id: str,
    user_id: str,
    task_ids: list[str] | None = None,
    scan_run_id: str | None = None,
) -> dict:
    """Upsert tasks into the user's TaskInbox.xlsx and rebuild view sheets."""
    q = (
        select(Task)
        .options(
            selectinload(Task.source_message),
            selectinload(Task.syncs),
            selectinload(Task.attachments),
        )
        .where(Task.tenant_id == tenant_id, Task.user_id == user_id)
    )
    if task_ids:
        q = q.where(Task.id.in_(task_ids))
    else:
        q = q.where(Task.status != TaskStatus.IGNORED.value)
    tasks = (await session.execute(q)).scalars().all()
    if not tasks:
        return {"synced": 0, "failed": 0, "workbook_url": None}

    client = await GraphClient.for_user(session, user_id, tenant_id)
    workbook_id: str | None = None
    url: str | None = None
    try:
        workbook = await excel_graph.find_or_create_task_workbook(client)
        workbook_id = workbook["id"]
        await excel_graph.ensure_workbook_layout(client, workbook_id)
        url = await excel_graph.get_workbook_url(client, workbook_id)

        rows = [_row_for_task(t) for t in tasks]
        try:
            inserted, updated = await excel_graph.upsert_task_rows(
                client, workbook_id, rows,
            )
        except Exception as exc:  # noqa: BLE001
            logger.exception("Excel upsert failed")
            for t in tasks:
                session.add(TaskSync(
                    tenant_id=tenant_id, user_id=user_id, task_id=t.id,
                    target_type=SyncTarget.EXCEL.value, target_url=url,
                    sync_status=SyncStatus.SYNC_FAILED.value,
                    error_message=str(exc)[:500],
                ))
            await session.commit()
            return {"synced": 0, "failed": len(tasks), "workbook_url": url}

        # Rebuild deterministic view sheets after upsert
        try:
            await excel_graph.rebuild_view_sheets(
                client, workbook_id,
                last_sync_at=datetime.utcnow(),
                scan_run_id=scan_run_id,
            )
        except Exception:  # noqa: BLE001
            logger.exception("View sheet rebuild failed (non-fatal)")

        # Record sync rows. We dedup at the row level: only one SYNCED row per task.
        for t in tasks:
            existing = next(
                (
                    s for s in (t.syncs or [])
                    if s.target_type == SyncTarget.EXCEL.value
                    and s.sync_status == SyncStatus.SYNCED.value
                ),
                None,
            )
            if existing:
                existing.target_url = url
                existing.synced_at = datetime.utcnow()
                existing.error_message = None
            else:
                session.add(TaskSync(
                    tenant_id=tenant_id, user_id=user_id, task_id=t.id,
                    target_type=SyncTarget.EXCEL.value, target_url=url,
                    sync_status=SyncStatus.SYNCED.value,
                    synced_at=datetime.utcnow(),
                ))
        await session.commit()
        return {
            "synced": inserted + updated,
            "inserted": inserted,
            "updated": updated,
            "failed": 0,
            "workbook_url": url,
        }
    finally:
        await client.aclose()


async def get_excel_status(
    session: AsyncSession, *, tenant_id: str, user_id: str,
) -> dict:
    res = await session.execute(
        select(TaskSync).where(
            TaskSync.tenant_id == tenant_id,
            TaskSync.user_id == user_id,
            TaskSync.target_type == SyncTarget.EXCEL.value,
            TaskSync.sync_status == SyncStatus.SYNCED.value,
        ).order_by(TaskSync.synced_at.desc()).limit(1)
    )
    last = res.scalar_one_or_none()
    return {
        "workbook_url": last.target_url if last else None,
        "last_sync_at": last.synced_at if last else None,
        "last_error": None,
    }
