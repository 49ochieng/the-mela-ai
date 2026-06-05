"""
Mela AI - Claude Daily Usage Tracking

Tracks per-user Claude question counts in a daily window (UTC).
Fast path: Redis INCR with midnight-UTC TTL (shared across replicas).
Fallback: DB upsert, then in-memory counter for MockSession / DB unavailable.
"""

import logging
import uuid
from datetime import datetime, timedelta, timezone
from typing import Optional

from app.core.config import settings

logger = logging.getLogger(__name__)

# In-memory fallback for MockSession / DB unavailable
_in_memory: dict[str, dict] = {}  # {user_id: {date: str, count: int, tokens: int}}


def _today_utc() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


def _seconds_until_midnight_utc() -> int:
    now = datetime.now(timezone.utc)
    midnight = (now + timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)
    return max(1, int((midnight - now).total_seconds()))


class ClaudeUsageService:
    """Upsert-based daily usage counters for Claude models."""

    async def get_usage(self, db, user_id: str) -> dict:
        """Return today's usage dict: {question_count, token_count, limit, remaining, date}.

        Checks Redis cache first for the question count, then falls through to DB.
        """
        today = _today_utc()
        limit = settings.CLAUDE_DAILY_QUESTION_LIMIT

        # ── Redis fast path ────────────────────────────────────────────────────
        try:
            from app.core.redis_client import get_redis, key as rkey

            r = await get_redis()
            if r is not None:
                rk_q = rkey("quota", "claude", user_id, today)
                cached_count = await r.get(rk_q)
                if cached_count is not None:
                    count = int(cached_count)
                    remaining = max(0, limit - count) if limit > 0 else -1
                    return {
                        "question_count": count,
                        "token_count": 0,  # token detail requires DB
                        "limit": limit,
                        "remaining": remaining,
                        "date": today,
                    }
        except Exception as exc:
            logger.debug("claude_usage Redis read error (%s)", exc)

        # ── DB path ────────────────────────────────────────────────────────────
        try:
            from app.models.models import ClaudeUsage
            from sqlalchemy import select

            result = await db.execute(
                select(ClaudeUsage).where(
                    ClaudeUsage.user_id == user_id,
                    ClaudeUsage.window_date == today,
                )
            )
            row: Optional[ClaudeUsage] = result.scalar_one_or_none()
            count = row.question_count if row else 0
            tokens = row.token_count if row else 0
        except Exception:
            # MockSession or DB error — use in-memory
            entry = _in_memory.get(user_id, {})
            if entry.get("date") != today:
                entry = {"date": today, "count": 0, "tokens": 0}
                _in_memory[user_id] = entry
            count = entry["count"]
            tokens = entry["tokens"]

        remaining = max(0, limit - count) if limit > 0 else -1
        return {
            "question_count": count,
            "token_count": tokens,
            "limit": limit,
            "remaining": remaining,
            "date": today,
        }

    async def check_allowed(self, db, user_id: str) -> tuple[bool, dict]:
        """Return (allowed, usage_dict). allowed=True if under daily limit."""
        usage = await self.get_usage(db, user_id)
        limit = usage["limit"]
        if limit <= 0:
            return True, usage  # unlimited
        allowed = usage["question_count"] < limit
        return allowed, usage

    async def record_question(self, db, user_id: str, tokens_used: int = 0) -> dict:
        """Increment question count + token count. Returns updated usage.

        Redis INCR is the fast path for the question counter.  DB remains
        the authoritative source for token totals (requires HINCRBYFLOAT
        precision) and for crash-safe persistence.
        """
        today = _today_utc()

        # ── Redis fast path (question counter) ───────────────────────────────
        try:
            from app.core.redis_client import get_redis, key as rkey

            r = await get_redis()
            if r is not None:
                rk_q = rkey("quota", "claude", user_id, today)
                new_count = await r.incr(rk_q)
                if new_count == 1:
                    await r.expire(rk_q, _seconds_until_midnight_utc())
        except Exception as exc:
            logger.debug("claude_usage Redis incr error (%s)", exc)

        # ── DB upsert (persistent, authoritative) ────────────────────────────
        try:
            from app.models.models import ClaudeUsage
            from sqlalchemy import select

            result = await db.execute(
                select(ClaudeUsage).where(
                    ClaudeUsage.user_id == user_id,
                    ClaudeUsage.window_date == today,
                )
            )
            row: Optional[ClaudeUsage] = result.scalar_one_or_none()
            if row is None:
                row = ClaudeUsage(
                    id=str(uuid.uuid4()),
                    user_id=user_id,
                    window_date=today,
                    question_count=1,
                    token_count=tokens_used,
                )
                db.add(row)
            else:
                row.question_count += 1
                row.token_count += tokens_used
                row.updated_at = datetime.utcnow()
            await db.commit()
            return await self.get_usage(db, user_id)
        except Exception as e:
            logger.warning("claude_usage DB write failed (%s), using in-memory", e)
            entry = _in_memory.get(user_id, {})
            if entry.get("date") != today:
                entry = {"date": today, "count": 0, "tokens": 0}
            entry["count"] += 1
            entry["tokens"] += tokens_used
            _in_memory[user_id] = entry
            limit = settings.CLAUDE_DAILY_QUESTION_LIMIT
            remaining = max(0, limit - entry["count"]) if limit > 0 else -1
            return {
                "question_count": entry["count"],
                "token_count": entry["tokens"],
                "limit": limit,
                "remaining": remaining,
                "date": today,
            }


claude_usage_service = ClaudeUsageService()
