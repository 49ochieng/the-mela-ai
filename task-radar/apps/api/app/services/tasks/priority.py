"""Deterministic priority scoring (0-100) and urgency bucketing.

Combines LLM-assigned priority + due-date proximity + source signal +
confidence into a single integer score so the dashboard / Excel can sort
"what should I focus on" without re-asking the LLM.
"""
from __future__ import annotations

from datetime import datetime, timedelta
from typing import Optional

from ...enums import Priority, SourceType


# Base weights — tuned so a high-priority task always outranks a medium one
# with the same urgency, and overdue always wins the day.
_BASE = {
    Priority.HIGH.value: 70,
    Priority.MEDIUM.value: 40,
    Priority.LOW.value: 15,
}


def urgency_bucket(due: Optional[datetime], now: Optional[datetime] = None) -> str:
    """Return one of: Overdue | Today | Tomorrow | ThisWeek | Later | NoDate."""
    if due is None:
        return "NoDate"
    now = now or datetime.utcnow()
    today = now.date()
    d = due.date()
    if d < today:
        return "Overdue"
    if d == today:
        return "Today"
    if d == today + timedelta(days=1):
        return "Tomorrow"
    if d <= today + timedelta(days=7):
        return "ThisWeek"
    return "Later"


def _urgency_bonus(bucket: str) -> int:
    return {
        "Overdue": 30,
        "Today": 25,
        "Tomorrow": 15,
        "ThisWeek": 8,
        "Later": 0,
        "NoDate": 0,
    }.get(bucket, 0)


def _source_bonus(source_type: str, is_mention: bool = False) -> int:
    if source_type == SourceType.TEAMS.value and is_mention:
        return 5
    if source_type == SourceType.EMAIL.value:
        return 3
    return 0


def compute_priority_score(
    *,
    priority: str,
    due_date: Optional[datetime],
    source_type: str,
    confidence: float = 0.0,
    is_mention: bool = False,
    now: Optional[datetime] = None,
) -> int:
    score = _BASE.get(priority, 40)
    bucket = urgency_bucket(due_date, now=now)
    score += _urgency_bonus(bucket)
    score += _source_bonus(source_type, is_mention=is_mention)
    score += int(round((confidence or 0.0) * 5))
    return max(0, min(100, score))
