"""
Mela AI - Budget Governance Service

Enforces per-user and per-tenant budget limits with warning and hard-stop thresholds.
Used by the chat middleware to check budget before processing requests.
"""

import logging
from typing import Optional, Dict
from datetime import datetime, timedelta
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func

from app.models.models import UserBudget, ModelUsage, Conversation
from app.services.billing_service import calculate_cost, get_cost_rates

logger = logging.getLogger(__name__)


class BudgetStatus:
    """Result of a budget check."""
    def __init__(
        self,
        allowed: bool = True,
        usage_pct: int = 0,
        warning: bool = False,
        hard_stop: bool = False,
        message: Optional[str] = None,
        budget_type: Optional[str] = None,
    ):
        self.allowed = allowed
        self.usage_pct = usage_pct
        self.warning = warning
        self.hard_stop = hard_stop
        self.message = message
        self.budget_type = budget_type

    def to_dict(self) -> dict:
        return {
            "allowed": self.allowed,
            "usage_pct": self.usage_pct,
            "warning": self.warning,
            "hard_stop": self.hard_stop,
            "message": self.message,
            "budget_type": self.budget_type,
        }


async def check_budget(
    db: AsyncSession,
    user_id: str,
    tenant_id: Optional[str] = None,
    fire_notifications: bool = True,
) -> BudgetStatus:
    """Check if user is within budget limits.

    Returns BudgetStatus with allowed=False if hard-stopped.
    When fire_notifications=True (default) and the user crosses a warning or
    hard-stop threshold, an in-app (and email) notification is fired — but only
    once per threshold crossing so the user is not spammed.

    Redis fast path: cached usage totals are checked first.  On cache miss the
    DB is queried and the result is written back to cache (write-on-read).
    """
    from app.core.budget_cache import get_usage_cached, set_usage_cache

    # Find applicable budget (user-specific first, then tenant-level)
    budget = await _get_budget(db, user_id, tenant_id)
    if not budget or not budget.is_active:
        return BudgetStatus(allowed=True)

    period_start = _get_period_start(budget.period)

    # ── Redis fast path ────────────────────────────────────────────────────────
    cached = await get_usage_cached(user_id)
    if cached is not None:
        usage = {
            "tokens": cached["daily_tokens"] if budget.period == "daily" else cached["monthly_tokens"],
            "requests": 0,  # request count not cached; only used for display
        }
        cost_hint = cached["daily_cost"] if budget.period == "daily" else cached["monthly_cost"]
    else:
        # ── DB path + write-on-read ────────────────────────────────────────────
        usage = await _get_usage_in_period(db, user_id, period_start)
        rates = await get_cost_rates(db)
        cost_hint = await _get_cost_in_period(db, user_id, period_start, rates)
        # Determine daily vs monthly totals for cache population.
        now_utc = datetime.utcnow()
        daily_start = now_utc.replace(hour=0, minute=0, second=0, microsecond=0)
        monthly_start = now_utc.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
        if budget.period == "daily":
            await set_usage_cache(
                user_id,
                daily_tokens=usage["tokens"],
                daily_cost=cost_hint,
                monthly_tokens=usage["tokens"],  # approximate; will be corrected on monthly hit
                monthly_cost=cost_hint,
            )
        else:
            daily_usage = await _get_usage_in_period(db, user_id, daily_start)
            daily_rates = await get_cost_rates(db)
            daily_cost = await _get_cost_in_period(db, user_id, daily_start, daily_rates)
            await set_usage_cache(
                user_id,
                daily_tokens=daily_usage["tokens"],
                daily_cost=daily_cost,
                monthly_tokens=usage["tokens"],
                monthly_cost=cost_hint,
            )

    # Check token budget
    if budget.token_budget and budget.token_budget > 0:
        token_pct = int((usage["tokens"] / budget.token_budget) * 100)
        if token_pct >= 100 and budget.hard_stop:
            if fire_notifications:
                await _maybe_notify_exceeded(db, user_id, "token", token_pct)
            return BudgetStatus(
                allowed=False,
                usage_pct=token_pct,
                hard_stop=True,
                message=(
                    f"Token budget exceeded "
                    f"({usage['tokens']:,} / {budget.token_budget:,} tokens). "
                    "Please contact your administrator."
                ),
                budget_type="token",
            )
        if token_pct >= budget.token_warning_pct:
            if fire_notifications:
                await _maybe_notify_warning(db, user_id, "token", token_pct)
            return BudgetStatus(
                allowed=True,
                usage_pct=token_pct,
                warning=True,
                message=(
                    f"You've used {token_pct}% of your token budget "
                    f"({usage['tokens']:,} / {budget.token_budget:,})."
                ),
                budget_type="token",
            )

    # Check cost budget
    if budget.cost_budget and budget.cost_budget > 0:
        cost = cost_hint
        if cached is None:
            pass  # cost already computed above
        else:
            # Recompute from DB for accuracy on near-threshold decisions.
            rates = await get_cost_rates(db)
            cost = await _get_cost_in_period(db, user_id, period_start, rates)
        cost_pct = int((cost / budget.cost_budget) * 100)
        if cost_pct >= 100 and budget.hard_stop:
            if fire_notifications:
                await _maybe_notify_exceeded(db, user_id, "cost", cost_pct)
            return BudgetStatus(
                allowed=False,
                usage_pct=cost_pct,
                hard_stop=True,
                message=(
                    f"Cost budget exceeded "
                    f"(${cost:.2f} / ${budget.cost_budget:.2f}). "
                    "Please contact your administrator."
                ),
                budget_type="cost",
            )
        if cost_pct >= budget.cost_warning_pct:
            if fire_notifications:
                await _maybe_notify_warning(db, user_id, "cost", cost_pct)
            return BudgetStatus(
                allowed=True,
                usage_pct=cost_pct,
                warning=True,
                message=(
                    f"You've used {cost_pct}% of your cost budget "
                    f"(${cost:.2f} / ${budget.cost_budget:.2f})."
                ),
                budget_type="cost",
            )

    return BudgetStatus(allowed=True)


# ── Notification helpers (deduplicated via DB) ───────────────────────────────

# In-process caches are warm-start optimisations only; the authoritative
# dedup check reads from the notifications table so restarts don't re-fire.
_notified_warning: set = set()
_notified_exceeded: set = set()


async def _notification_already_sent(
    db: AsyncSession, user_id: str, notif_type: str, dedup_key: str
) -> bool:
    """Check if a notification with the given dedup key exists in the current period."""
    from app.models.models import Notification, NotificationType
    ntype = (
        NotificationType.BUDGET_WARNING
        if notif_type == "warning"
        else NotificationType.BUDGET_EXCEEDED
    )
    # Look for a matching notification created in the last 24 hours
    cutoff = datetime.utcnow() - timedelta(hours=24)
    stmt = select(func.count()).select_from(Notification).where(
        Notification.user_id == user_id,
        Notification.type == ntype,
        Notification.message.contains(dedup_key),
        Notification.created_at >= cutoff,
    )
    result = await db.execute(stmt)
    return (result.scalar() or 0) > 0


async def _maybe_notify_warning(
    db: AsyncSession, user_id: str, budget_type: str, usage_pct: int
) -> None:
    """Fire a budget-warning notification if not already sent for this bucket."""
    bucket = (usage_pct // 10) * 10   # e.g. 73% → bucket 70
    key = (user_id, budget_type, bucket)
    if key in _notified_warning:
        return
    # DB-level dedup so restarts don't re-fire
    dedup_key = f"{budget_type}:{bucket}%"
    if await _notification_already_sent(db, user_id, "warning", dedup_key):
        _notified_warning.add(key)
        return
    _notified_warning.add(key)
    try:
        from app.services.notification_service import notify_budget_warning
        await notify_budget_warning(db, user_id, usage_pct, budget_type)
        await db.flush()
    except Exception as exc:
        logger.warning("Failed to send budget warning notification: %s", exc)


async def _maybe_notify_exceeded(
    db: AsyncSession, user_id: str, budget_type: str, usage_pct: int
) -> None:
    """Fire a budget-exceeded notification if not already sent."""
    key = (user_id, budget_type, "exceeded")
    if key in _notified_exceeded:
        return
    dedup_key = f"{budget_type}:exceeded"
    if await _notification_already_sent(db, user_id, "exceeded", dedup_key):
        _notified_exceeded.add(key)
        return
    _notified_exceeded.add(key)
    try:
        from app.services.notification_service import notify_budget_exceeded
        await notify_budget_exceeded(db, user_id, budget_type)
        await db.flush()
    except Exception as exc:
        logger.warning("Failed to send budget exceeded notification: %s", exc)


async def get_user_budget_summary(
    db: AsyncSession,
    user_id: str,
    tenant_id: Optional[str] = None,
) -> Optional[dict]:
    """Get budget summary for display in the frontend."""
    budget = await _get_budget(db, user_id, tenant_id)
    if not budget or not budget.is_active:
        return None

    period_start = _get_period_start(budget.period)
    usage = await _get_usage_in_period(db, user_id, period_start)
    rates = await get_cost_rates(db)
    cost = await _get_cost_in_period(db, user_id, period_start, rates)

    return {
        "period": budget.period,
        "token_budget": budget.token_budget,
        "token_used": usage["tokens"],
        "token_warning_pct": budget.token_warning_pct,
        "cost_budget": budget.cost_budget,
        "cost_used": round(cost, 4),
        "cost_warning_pct": budget.cost_warning_pct,
        "hard_stop": budget.hard_stop,
        "requests": usage["requests"],
    }


# ── Admin CRUD ───────────────────────────────────────────────────────────────

async def set_user_budget(
    db: AsyncSession,
    user_id: str,
    admin_id: str,
    token_budget: Optional[int] = None,
    cost_budget: Optional[float] = None,
    period: str = "monthly",
    hard_stop: bool = False,
    token_warning_pct: int = 80,
    cost_warning_pct: int = 80,
) -> UserBudget:
    """Set or update a user's budget."""
    result = await db.execute(
        select(UserBudget).where(UserBudget.user_id == user_id)
    )
    budget = result.scalar_one_or_none()

    if budget:
        budget.token_budget = token_budget
        budget.cost_budget = cost_budget
        budget.period = period
        budget.hard_stop = hard_stop
        budget.token_warning_pct = token_warning_pct
        budget.cost_warning_pct = cost_warning_pct
        budget.updated_at = datetime.utcnow()
    else:
        budget = UserBudget(
            user_id=user_id,
            token_budget=token_budget,
            cost_budget=cost_budget,
            period=period,
            hard_stop=hard_stop,
            token_warning_pct=token_warning_pct,
            cost_warning_pct=cost_warning_pct,
            created_by=admin_id,
        )
        db.add(budget)

    await db.flush()
    return budget


# ── Private helpers ──────────────────────────────────────────────────────────

async def _get_budget(
    db: AsyncSession,
    user_id: str,
    tenant_id: Optional[str] = None,
) -> Optional[UserBudget]:
    """Get the most specific budget for a user (user > tenant)."""
    # User-specific budget first
    result = await db.execute(
        select(UserBudget)
        .where(UserBudget.user_id == user_id, UserBudget.is_active == True)
    )
    budget = result.scalar_one_or_none()
    if budget:
        return budget

    # Fall back to tenant budget
    if tenant_id:
        result = await db.execute(
            select(UserBudget)
            .where(
                UserBudget.tenant_id == tenant_id,
                UserBudget.user_id == None,
                UserBudget.is_active == True,
            )
        )
        return result.scalar_one_or_none()

    return None


def _get_period_start(period: str) -> datetime:
    """Get the start of the current budget period."""
    now = datetime.utcnow()
    if period == "daily":
        return now.replace(hour=0, minute=0, second=0, microsecond=0)
    else:  # monthly
        return now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)


async def _get_usage_in_period(
    db: AsyncSession,
    user_id: str,
    period_start: datetime,
) -> Dict:
    """Get token usage and request count in the current period."""
    result = await db.execute(
        select(
            func.coalesce(func.sum(ModelUsage.total_tokens), 0),
            func.count(ModelUsage.id),
        )
        .where(
            ModelUsage.user_id == user_id,
            ModelUsage.created_at >= period_start,
        )
    )
    row = result.one()
    return {"tokens": int(row[0]), "requests": int(row[1])}


async def _get_cost_in_period(
    db: AsyncSession,
    user_id: str,
    period_start: datetime,
    rates: Dict[str, float],
) -> float:
    """Calculate total cost in the current period."""
    result = await db.execute(
        select(ModelUsage.model, func.sum(ModelUsage.total_tokens))
        .where(
            ModelUsage.user_id == user_id,
            ModelUsage.created_at >= period_start,
        )
        .group_by(ModelUsage.model)
    )
    total_cost = 0.0
    for model, tokens in result.fetchall():
        tokens = tokens or 0
        total_cost += calculate_cost(tokens, model, rates)
    return total_cost
