"""Planner sync — idempotent create/update with priority + categories."""
from __future__ import annotations

import logging
from datetime import datetime
from typing import Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from ...enums import Priority, SourceType, SyncStatus, SyncTarget, TaskStatus
from ...models import ScanSettings, Task, TaskSync
from ..graph import planner as planner_graph
from ..graph.client import GraphClient

logger = logging.getLogger(__name__)


# ── policy helper ────────────────────────────────────────────────────
def task_eligible_for_auto_planner(task: Task, policy: str) -> bool:
    p = (policy or "none").lower()
    if p == "all":
        return True
    if p == "high_medium":
        return task.priority in (Priority.HIGH.value, Priority.MEDIUM.value)
    if p == "high":
        return task.priority == Priority.HIGH.value
    return False


def _build_checklist(task: Task) -> list[str]:
    """Try to extract bullet/numbered items from description."""
    desc = (task.description or "").strip()
    if not desc:
        return []
    items: list[str] = []
    for line in desc.splitlines():
        s = line.strip()
        if not s:
            continue
        if s[:2] in ("- ", "* "):
            items.append(s[2:].strip())
        elif len(s) > 3 and s[0].isdigit() and s[1] in (".", ")"):
            items.append(s[2:].strip())
    return items[:20]


def _build_references(task: Task) -> list[tuple[str, str]]:
    refs: list[tuple[str, str]] = []
    if task.source_link:
        label = "Open original message"
        if task.source_type == SourceType.TEAMS.value:
            label = "Open in Teams"
        elif task.source_type == SourceType.EMAIL.value:
            label = "Open in Outlook"
        refs.append((label, task.source_link))
    for a in (task.attachments or []):
        url = a.archive_url or a.source_url
        if url:
            refs.append((a.file_name, url))
    return refs


def _category_indices(task: Task) -> list[int]:
    """Map source/type to plan category labels 1..3.

    1 = source kind (email/teams), 2 = priority, 3 = task_type bucket.
    Plans without these labels just won't show colored chips — harmless.
    """
    cats: list[int] = []
    cats.append(1 if task.source_type == SourceType.EMAIL.value else 2)
    if task.priority == Priority.HIGH.value:
        cats.append(3)
    return sorted(set(cats))


# ── single-task create/upsert ────────────────────────────────────────
async def create_planner_task(
    session: AsyncSession,
    *,
    tenant_id: str,
    user_id: str,
    task_id: str,
    plan_id: Optional[str] = None,
    bucket_id: Optional[str] = None,
) -> dict:
    task = await session.get(
        Task, task_id, options=[selectinload(Task.syncs), selectinload(Task.attachments)]
    )
    if task is None or task.tenant_id != tenant_id or task.user_id != user_id:
        raise PermissionError("Task not found in scope")
    settings = (await session.execute(
        select(ScanSettings).where(
            ScanSettings.tenant_id == tenant_id, ScanSettings.user_id == user_id,
        )
    )).scalar_one_or_none()
    plan_id = plan_id or (settings.planner_plan_id if settings else None)
    bucket_id = bucket_id or (settings.planner_bucket_id if settings else None)

    client = await GraphClient.for_user(session, user_id, tenant_id)

    # Auto-bootstrap: if no plan configured, create "Mela Task Radar" plan
    # with "Email Tasks" + "Teams Tasks" buckets and persist to settings.
    bucket_map: dict[str, str] = {}
    if not plan_id:
        try:
            ensured = await planner_graph.ensure_default_plan_with_buckets(client)
            plan_id = ensured["plan_id"]
            bucket_map = ensured["buckets"]
            if settings is not None:
                settings.planner_plan_id = plan_id
                # Persist Email Tasks bucket as default; Teams will be picked
                # per-task below based on source_type.
                if not settings.planner_bucket_id:
                    settings.planner_bucket_id = bucket_map.get("Email Tasks")
                await session.commit()
        except Exception as e:  # noqa: BLE001
            await client.aclose()
            logger.exception("Failed to auto-create Planner plan")
            session.add(TaskSync(
                tenant_id=tenant_id, user_id=user_id, task_id=task.id,
                target_type=SyncTarget.PLANNER.value,
                sync_status=SyncStatus.SYNC_FAILED.value,
                error_message=f"Could not provision Planner plan: {e}"[:500],
            ))
            await session.commit()
            return {"planner_url": None, "sync_status": "sync_failed",
                    "error": str(e)[:500]}

    # If no explicit bucket, route by source: Teams → "Teams Tasks",
    # everything else → "Email Tasks". Falls back to plan's first bucket.
    if not bucket_id:
        if not bucket_map:
            try:
                buckets = await planner_graph.list_buckets(client, plan_id)
                bucket_map = {b["name"]: b["id"] for b in buckets}
            except Exception:  # noqa: BLE001
                bucket_map = {}
        if task.source_type == SourceType.TEAMS.value:
            bucket_id = bucket_map.get("Teams Tasks") or bucket_map.get("Email Tasks")
        else:
            bucket_id = bucket_map.get("Email Tasks") or bucket_map.get("Teams Tasks")

    if not plan_id:
        await client.aclose()
        raise ValueError("No Planner plan configured")

    # If already synced, PATCH instead of recreate
    existing = next(
        (s for s in (task.syncs or [])
         if s.target_type == SyncTarget.PLANNER.value
         and s.sync_status == SyncStatus.SYNCED.value
         and s.target_id),
        None,
    )

    try:
        if existing:
            try:
                await planner_graph.update_task(
                    client, existing.target_id,
                    title=task.title,
                    due_date=task.due_date,
                    priority=task.priority,
                    percent_complete=100 if task.status == TaskStatus.DONE.value else None,
                )
                existing.synced_at = datetime.utcnow()
                existing.error_message = None
                await session.commit()
                return {"planner_url": existing.target_url, "sync_status": "synced"}
            except Exception as e:  # noqa: BLE001
                logger.exception("Planner update failed; will retry as create")
                existing.sync_status = SyncStatus.SYNC_FAILED.value
                existing.error_message = str(e)[:500]
                await session.commit()
                # Fall through and try create

        try:
            resolved_bucket = await planner_graph.resolve_bucket(
                client, plan_id, bucket_id, task.priority,
            )
            description_full = "\n\n".join(
                p for p in [
                    task.description or "",
                    f"Why this priority: {task.priority_reasoning}" if task.priority_reasoning else "",
                    f"Evidence: \"{task.evidence}\"" if task.evidence else "",
                    f"Source: {task.source_type} (confidence {task.confidence:.2f})",
                ] if p
            )
            created = await planner_graph.create_task(
                client, plan_id, resolved_bucket,
                task.title, task.due_date, description_full,
                priority=task.priority,
                checklist=_build_checklist(task),
                references=_build_references(task),
                category_indices=_category_indices(task),
            )
            url = planner_graph.get_planner_task_url(created["id"])
            session.add(TaskSync(
                tenant_id=tenant_id, user_id=user_id, task_id=task.id,
                target_type=SyncTarget.PLANNER.value, target_id=created["id"],
                target_url=url, sync_status=SyncStatus.SYNCED.value,
                synced_at=datetime.utcnow(),
            ))
            await session.commit()
            return {"planner_url": url, "sync_status": "synced"}
        except Exception as e:  # noqa: BLE001
            logger.exception("Planner create failed")
            session.add(TaskSync(
                tenant_id=tenant_id, user_id=user_id, task_id=task.id,
                target_type=SyncTarget.PLANNER.value,
                sync_status=SyncStatus.SYNC_FAILED.value,
                error_message=str(e)[:500],
            ))
            await session.commit()
            return {"planner_url": None, "sync_status": "sync_failed"}
    finally:
        await client.aclose()


# ── batch sync (called from scan _finalize) ──────────────────────────
async def sync_tasks_to_planner(
    session: AsyncSession,
    *,
    tenant_id: str,
    user_id: str,
    task_ids: list[str],
    plan_id: Optional[str] = None,
    bucket_id: Optional[str] = None,
) -> dict:
    if not task_ids:
        return {"synced": 0, "failed": 0}
    synced = 0
    failed = 0
    for tid in task_ids:
        try:
            r = await create_planner_task(
                session, tenant_id=tenant_id, user_id=user_id,
                task_id=tid, plan_id=plan_id, bucket_id=bucket_id,
            )
            if r.get("sync_status") == "synced":
                synced += 1
            else:
                failed += 1
        except Exception:  # noqa: BLE001
            logger.exception("Planner sync failed for task %s", tid)
            failed += 1
    return {"synced": synced, "failed": failed}
