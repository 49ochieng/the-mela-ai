"""Layered deduplication checks against the database."""
from __future__ import annotations

from datetime import timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ...models import SourceMessage, Task


async def message_already_seen(
    session: AsyncSession, *, tenant_id: str, user_id: str, source_type: str,
    graph_message_id: str, internet_message_id: str | None, body_hash: str | None,
    received_at,
) -> bool:
    # 1) source_type + graph_message_id
    res = await session.execute(
        select(SourceMessage.id).where(
            SourceMessage.tenant_id == tenant_id,
            SourceMessage.user_id == user_id,
            SourceMessage.source_type == source_type,
            SourceMessage.graph_message_id == graph_message_id,
        )
    )
    if res.first():
        return True
    # 2) internet_message_id
    if internet_message_id:
        res = await session.execute(
            select(SourceMessage.id).where(
                SourceMessage.tenant_id == tenant_id,
                SourceMessage.user_id == user_id,
                SourceMessage.internet_message_id == internet_message_id,
            )
        )
        if res.first():
            return True
    # 3) body hash within ±5m
    if body_hash and received_at:
        lo = received_at - timedelta(minutes=5)
        hi = received_at + timedelta(minutes=5)
        res = await session.execute(
            select(SourceMessage.id).where(
                SourceMessage.tenant_id == tenant_id,
                SourceMessage.user_id == user_id,
                SourceMessage.body_hash == body_hash,
                SourceMessage.received_at.between(lo, hi),
            )
        )
        if res.first():
            return True
    return False


async def task_already_exists_for_message(
    session: AsyncSession, *, source_message_id: str, title: str
) -> bool:
    res = await session.execute(
        select(Task.id).where(
            Task.source_message_id == source_message_id,
            Task.title == title,
        )
    )
    return res.first() is not None
