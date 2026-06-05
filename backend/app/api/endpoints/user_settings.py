"""
Mela AI - User Settings & Usage Endpoints
"""

import json
import logging
from datetime import datetime, timedelta
from fastapi import APIRouter, Depends
from fastapi.responses import JSONResponse
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func, delete

from app.core.database import get_db
from app.core.config import settings as app_settings
from app.core.security import get_current_user
from app.models import User, Conversation, Message, ModelUsage, SystemSettings
from app.schemas.auth import UserInfo
from app.schemas.settings import (
    UserUsageResponse,
    UserDailyUsage,
    ModelBreakdown,
    CostBreakdown,
    UserPreferences,
    UserFeaturesResponse,
    OrgSettings,
)

logger = logging.getLogger(__name__)
router = APIRouter()

# Estimated Azure OpenAI pricing per 1K tokens: (input_cost, output_cost)
MODEL_PRICING: dict[str, tuple[float, float]] = {
    "gpt-5.2-chat": (0.01, 0.03),
    "gpt-4.1": (0.002, 0.008),
    "gpt-4o": (0.0025, 0.01),
    "kimi-k2.5": (0.001, 0.003),
    "mistral-large-3": (0.002, 0.006),
    "dall-e-3": (0.04, 0.0),
}
_DEFAULT_PRICING = (0.002, 0.008)


def _estimate_cost(model: str, prompt_tokens: int, completion_tokens: int) -> float:
    inp, out = MODEL_PRICING.get(model, _DEFAULT_PRICING)
    return (prompt_tokens / 1000) * inp + (completion_tokens / 1000) * out


_PREFS_KEY_PREFIX = "user_prefs:"


def _prefs_key(user_id: str) -> str:
    return f"{_PREFS_KEY_PREFIX}{user_id}"


# ── Usage ────────────────────────────────────────────────────────────────────

@router.get("/usage", response_model=UserUsageResponse)
async def get_user_usage(
    days: int = 30,
    tz_offset: int = 0,  # client's UTC offset in minutes (e.g. 180 for UTC+3)
    current_user: UserInfo = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Get usage statistics for the current user."""
    uid = current_user.id
    days = max(1, min(days, 365))
    tz_offset = max(-720, min(840, tz_offset))  # clamp to valid UTC range

    from collections import defaultdict

    # Local "now" in the user's timezone
    tz_delta = timedelta(minutes=tz_offset)
    local_now = datetime.utcnow() + tz_delta
    today_local = local_now.date()

    # UTC boundaries for "today" in the user's local time
    today_start_utc = datetime(today_local.year, today_local.month, today_local.day) - tz_delta
    today_end_utc = today_start_utc + timedelta(days=1)

    # UTC start of the selected period.
    # days=1 → just today; days=7 → today + 6 previous days, etc.
    period_start_local = (local_now - timedelta(days=days - 1)).date()
    period_start_utc = datetime(period_start_local.year, period_start_local.month, period_start_local.day) - tz_delta

    # ── Tokens used today (local timezone) ──────────────────────────────────
    tokens_today = await db.scalar(
        select(func.sum(ModelUsage.total_tokens))
        .where(ModelUsage.user_id == uid)
        .where(ModelUsage.created_at >= today_start_utc)
        .where(ModelUsage.created_at < today_end_utc)
    ) or 0

    # Daily token limit from User record
    user_row = await db.scalar(select(User).where(User.id == uid))
    daily_limit = user_row.daily_token_limit if user_row else app_settings.DEFAULT_DAILY_TOKEN_LIMIT

    # ── Fetch all ModelUsage rows for the period in one query ────────────────
    usage_rows_result = await db.execute(
        select(ModelUsage)
        .where(ModelUsage.user_id == uid)
        .where(ModelUsage.created_at >= period_start_utc)
    )
    usage_rows = usage_rows_result.scalars().all()

    # ── Build daily_map keyed by local date strings ──────────────────────────
    daily_map: dict[str, dict] = {}
    for i in range(days):
        d = (period_start_local + timedelta(days=i))
        daily_map[str(d)] = {
            "date": d, "conversations": 0, "messages": 0, "tokens": 0,
            "prompt_tokens": 0, "completion_tokens": 0, "estimated_cost": 0.0,
        }

    model_map: dict[str, dict] = defaultdict(lambda: {
        "request_count": 0, "total_tokens": 0, "prompt_tokens": 0,
        "completion_tokens": 0, "estimated_cost": 0.0,
    })
    hour_counts: dict[int, int] = defaultdict(int)

    total_tokens_all = 0
    total_prompt_all = 0
    total_completion_all = 0
    total_cost_all = 0.0
    total_requests = 0

    for row in usage_rows:
        pt = row.prompt_tokens or 0
        ct = row.completion_tokens or 0
        tt = row.total_tokens or 0
        cost = _estimate_cost(row.model or "unknown", pt, ct)

        total_tokens_all += tt
        total_prompt_all += pt
        total_completion_all += ct
        total_cost_all += cost
        total_requests += 1

        if row.created_at:
            # Convert UTC timestamp to the user's local time for bucketing
            local_ts = row.created_at + tz_delta
            d_str = str(local_ts.date())
            hour_counts[local_ts.hour] += 1
            if d_str in daily_map:
                daily_map[d_str]["tokens"] += tt
                daily_map[d_str]["prompt_tokens"] += pt
                daily_map[d_str]["completion_tokens"] += ct
                daily_map[d_str]["estimated_cost"] += cost

        m = row.model or "unknown"
        model_map[m]["request_count"] += 1
        model_map[m]["total_tokens"] += tt
        model_map[m]["prompt_tokens"] += pt
        model_map[m]["completion_tokens"] += ct
        model_map[m]["estimated_cost"] += cost

    # ── Count conversations/messages per local day in one pass (no N+1) ─────
    conv_ts_result = await db.execute(
        select(Conversation.created_at)
        .where(Conversation.user_id == uid)
        .where(Conversation.is_private == False)
        .where(Conversation.created_at >= period_start_utc)
    )
    for ts in conv_ts_result.scalars():
        if ts:
            d_str = str((ts + tz_delta).date())
            if d_str in daily_map:
                daily_map[d_str]["conversations"] += 1

    msg_ts_result = await db.execute(
        select(Message.created_at)
        .select_from(Message)
        .join(Conversation, Message.conversation_id == Conversation.id)
        .where(Conversation.user_id == uid)
        .where(Conversation.is_private == False)
        .where(Message.created_at >= period_start_utc)
    )
    for ts in msg_ts_result.scalars():
        if ts:
            d_str = str((ts + tz_delta).date())
            if d_str in daily_map:
                daily_map[d_str]["messages"] += 1

    # ── Totals for the selected period (consistent with range filter) ────────
    total_conversations = sum(v["conversations"] for v in daily_map.values())
    total_messages = sum(v["messages"] for v in daily_map.values())

    daily_rows = [UserDailyUsage(**v) for v in sorted(daily_map.values(), key=lambda x: x["date"])]

    model_breakdown = [
        ModelBreakdown(
            model=m,
            request_count=d["request_count"],
            total_tokens=d["total_tokens"],
            prompt_tokens=d["prompt_tokens"],
            completion_tokens=d["completion_tokens"],
            estimated_cost=round(d["estimated_cost"], 4),
        )
        for m, d in sorted(model_map.items(), key=lambda x: -x[1]["estimated_cost"])
    ]

    cost_by_model = [
        CostBreakdown(
            category=m,
            cost=round(d["estimated_cost"], 4),
            tokens=d["total_tokens"],
            requests=d["request_count"],
        )
        for m, d in sorted(model_map.items(), key=lambda x: -x[1]["estimated_cost"])
    ]

    peak_hour = max(hour_counts, key=hour_counts.get) if hour_counts else -1
    avg_tpr = total_tokens_all / total_requests if total_requests else 0.0
    avg_cpr = total_cost_all / total_requests if total_requests else 0.0
    efficiency = total_completion_all / total_prompt_all if total_prompt_all else 0.0

    return UserUsageResponse(
        total_conversations=total_conversations,
        total_messages=total_messages,
        tokens_used_today=tokens_today,
        daily_token_limit=daily_limit,
        daily_usage=daily_rows,
        model_breakdown=model_breakdown,
        total_tokens=total_tokens_all,
        total_prompt_tokens=total_prompt_all,
        total_completion_tokens=total_completion_all,
        total_requests=total_requests,
        estimated_total_cost=round(total_cost_all, 4),
        avg_tokens_per_request=round(avg_tpr, 1),
        avg_cost_per_request=round(avg_cpr, 6),
        cost_by_model=cost_by_model,
        peak_hour=peak_hour,
        token_efficiency_ratio=round(efficiency, 3),
    )


# ── Preferences ──────────────────────────────────────────────────────────────

@router.get("/preferences", response_model=UserPreferences)
async def get_user_preferences(
    current_user: UserInfo = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Get user preferences."""
    key = _prefs_key(current_user.id)
    row = await db.scalar(select(SystemSettings).where(SystemSettings.key == key))
    if row:
        try:
            return UserPreferences(**json.loads(row.value))
        except Exception:
            pass
    return UserPreferences()


@router.put("/preferences", response_model=UserPreferences)
async def update_user_preferences(
    prefs: UserPreferences,
    current_user: UserInfo = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Update user preferences."""
    key = _prefs_key(current_user.id)
    row = await db.scalar(select(SystemSettings).where(SystemSettings.key == key))
    value = prefs.model_dump_json()

    if row:
        row.value = value
        row.updated_by = current_user.id
        row.updated_at = datetime.utcnow()
    else:
        db.add(SystemSettings(
            key=key,
            value=value,
            description=f"User preferences for {current_user.email}",
            updated_by=current_user.id,
        ))

    await db.commit()
    return prefs


# ── Features ─────────────────────────────────────────────────────────────────

_ORG_SETTINGS_KEY = "org_settings"


@router.get("/features", response_model=UserFeaturesResponse)
async def get_user_features(
    current_user: UserInfo = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Get feature flags and role for the current user."""
    # Authoritative: check DB role first (covers bootstrap-elevated users who have no
    # Entra App Role assignment). Fall back to token claims for service principals / dev.
    from app.models.models import User as UserModel, UserRole as _UserRole
    db_role = "user"
    try:
        db_user = await db.scalar(select(UserModel).where(UserModel.azure_id == current_user.id))
        if db_user is not None and db_user.role == _UserRole.ADMIN:
            db_role = "admin"
    except Exception:
        pass
    token_role = "admin" if ("Admin" in current_user.roles or "admin" in current_user.roles) else "user"
    role = "admin" if (db_role == "admin" or token_role == "admin") else "user"

    # Load org-level settings to check private_chat_enabled
    org_defaults = OrgSettings()
    try:
        org_row = await db.scalar(
            select(SystemSettings).where(SystemSettings.key == _ORG_SETTINGS_KEY)
        )
        if org_row:
            merged = {**org_defaults.model_dump(), **json.loads(org_row.value)}
            org = OrgSettings(**merged)
        else:
            org = org_defaults
    except Exception:
        org = org_defaults

    return UserFeaturesResponse(
        role=role,
        sso_configured=bool(app_settings.AZURE_TENANT_ID and app_settings.AZURE_CLIENT_ID),
        features={
            "voice": app_settings.ENABLE_VOICE,
            "file_upload": app_settings.ENABLE_FILE_UPLOAD,
            "agents": app_settings.ENABLE_AGENTS,
            "image_generation": app_settings.ENABLE_IMAGE_GENERATION,
            "translation": app_settings.ENABLE_TRANSLATION,
            "document_intelligence": app_settings.ENABLE_DOCUMENT_INTELLIGENCE,
            "rag": app_settings.ENABLE_RAG,
            "web_search": True,  # DuckDuckGo - always available, no key required
            "sharepoint_sync": app_settings.ENABLE_SHAREPOINT_SYNC,
            "private_chat": app_settings.ENABLE_PRIVATE_CHAT and org.private_chat_enabled,
        },
    )


# ── History management ───────────────────────────────────────────────────────

@router.delete("/history")
async def delete_user_history(
    current_user: UserInfo = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Delete all conversations for the current user."""
    await db.execute(
        delete(Conversation).where(Conversation.user_id == current_user.id)
    )
    await db.commit()
    return {"detail": "All conversations deleted."}


@router.get("/export")
async def export_user_data(
    current_user: UserInfo = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Export all user data as JSON."""
    result = await db.execute(
        select(Conversation)
        .where(Conversation.user_id == current_user.id)
        .where(Conversation.is_private == False)
        .order_by(Conversation.created_at.desc())
    )
    conversations = result.scalars().all()

    export = []
    for conv in conversations:
        msg_result = await db.execute(
            select(Message)
            .where(Message.conversation_id == conv.id)
            .order_by(Message.created_at)
        )
        msgs = msg_result.scalars().all()
        export.append({
            "id": conv.id,
            "title": conv.title,
            "model": conv.model,
            "created_at": conv.created_at.isoformat(),
            "messages": [
                {
                    "role": m.role,
                    "content": m.content,
                    "created_at": m.created_at.isoformat() if m.created_at else None,
                }
                for m in msgs
            ],
        })

    return JSONResponse(
        content={"user": current_user.email, "exported_at": datetime.utcnow().isoformat(), "conversations": export},
        headers={"Content-Disposition": f"attachment; filename=mela-export-{current_user.id[:8]}.json"},
    )
