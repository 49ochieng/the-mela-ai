"""Persist tasks from extraction results."""
from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import and_, select
from sqlalchemy.ext.asyncio import AsyncSession

from ...enums import Priority, SourceType, TaskStatus, TaskType
from ...models import SourceMessage, Task
from ...schemas import ExtractedTask, ExtractionResult
from .audit import log
from .dedup import task_already_exists_for_message
from .priority import compute_priority_score


LOW_CONFIDENCE_THRESHOLD = 0.65


def _parse_due(d: str | None) -> datetime | None:
    if not d:
        return None
    try:
        return datetime.fromisoformat(d)
    except ValueError:
        try:
            return datetime.strptime(d, "%Y-%m-%d")
        except ValueError:
            return None


async def persist_extraction(
    session: AsyncSession,
    *,
    tenant_id: str,
    user_id: str,
    source_message: SourceMessage,
    extraction: ExtractionResult,
) -> tuple[list[Task], int]:
    """Returns (created_tasks, deduped_count)."""
    if not extraction.has_task or not extraction.tasks:
        return [], 0

    created: list[Task] = []
    deduped = 0
    for et in extraction.tasks:
        if await task_already_exists_for_message(
            session, source_message_id=source_message.id, title=et.title
        ):
            deduped += 1
            continue
        status = TaskStatus.NEEDS_REVIEW if et.confidence < LOW_CONFIDENCE_THRESHOLD else TaskStatus.OPEN
        due_dt = _parse_due(et.due_date)
        is_mention = bool(
            (source_message.raw_metadata_json or {}).get("is_mention")
        )
        score = compute_priority_score(
            priority=et.priority.value,
            due_date=due_dt,
            source_type=source_message.source_type,
            confidence=float(et.confidence),
            is_mention=is_mention,
        )
        task = Task(
            tenant_id=tenant_id,
            user_id=user_id,
            source_message_id=source_message.id,
            title=et.title[:512],
            description=et.description,
            task_type=et.task_type.value,
            assigned_to=et.assigned_to,
            due_date=due_dt,
            due_date_raw=et.due_date_raw,
            priority=et.priority.value,
            priority_reasoning=et.priority_reasoning,
            priority_score=score,
            confidence=float(et.confidence),
            evidence=et.evidence,
            status=status.value,
            source_type=source_message.source_type,
            source_link=source_message.source_link,
        )
        session.add(task)
        await session.flush()
        await log(
            session,
            tenant_id=tenant_id,
            user_id=user_id,
            action="task.created",
            entity_type="task",
            entity_id=task.id,
            details={"title": task.title, "source_type": task.source_type},
        )
        created.append(task)
    return created, deduped
