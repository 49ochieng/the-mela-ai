"""
Mela AI - Billing / Cost Tracking Service

Cost rates are stored in the model_quota_policies table.
This service provides helpers for reading rates, estimating costs,
and seeding default rates on first run.
"""

import logging
from typing import Dict, Optional
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select

from app.models.models import ModelQuotaPolicy, ModelUsage

logger = logging.getLogger(__name__)

# ── Default cost rates (USD per 1 000 tokens) ────────────────────────────────
# Prompt + completion tokens are weighted equally as a simplification.
# Operators can override these via the admin Model Governance panel.

DEFAULT_COST_RATES: Dict[str, float] = {
    "gpt-5.2-chat":       0.0025,
    "gpt-4.1":            0.0015,
    "gpt-4o":             0.0020,
    "kimi-k2.5":          0.0010,
    "mistral-large-3":    0.0008,
    "grok-3-mini":        0.0003,
    "llama-4-maverick":   0.0005,
    "gemini-2.0-flash":   0.0000,   # free tier
    "claude-opus-4-6":    0.0150,
    "claude-sonnet-4-6":  0.0030,
    "claude-haiku-4-5":   0.0008,
    "dall-e-3":           0.0400,   # per image; treat "tokens" as 1 for images
}

DEFAULT_DISPLAY_NAMES: Dict[str, str] = {
    "gpt-5.2-chat":       "GPT-5.2 Chat",
    "gpt-4.1":            "GPT-4.1",
    "gpt-4o":             "GPT-4o",
    "kimi-k2.5":          "Kimi K2.5",
    "mistral-large-3":    "Mistral Large 3",
    "grok-3-mini":        "Grok-3-mini",
    "llama-4-maverick":   "Llama 4 Maverick",
    "gemini-2.0-flash":   "Gemini 2.0 Flash",
    "claude-opus-4-6":    "Claude Opus 4.6",
    "claude-sonnet-4-6":  "Claude Sonnet 4.6",
    "claude-haiku-4-5":   "Claude Haiku 4.5",
    "dall-e-3":           "DALL-E 3",
}

DEFAULT_PROVIDERS: Dict[str, str] = {
    "gpt-5.2-chat":       "azure_openai",
    "gpt-4.1":            "azure_openai",
    "gpt-4o":             "azure_openai",
    "kimi-k2.5":          "azure_ai_foundry",
    "mistral-large-3":    "azure_ai_foundry",
    "grok-3-mini":        "azure_ai_foundry",
    "llama-4-maverick":   "azure_ai_foundry",
    "gemini-2.0-flash":   "google",
    "claude-opus-4-6":    "anthropic",
    "claude-sonnet-4-6":  "anthropic",
    "claude-haiku-4-5":   "anthropic",
    "dall-e-3":           "azure_openai",
}


# ── Public helpers ───────────────────────────────────────────────────────────

def calculate_cost(tokens: int, model: str, rates: Dict[str, float]) -> float:
    """Return estimated USD cost for the given token count + model."""
    rate = rates.get(model) or rates.get("gpt-4.1") or 0.002
    return round((tokens / 1000.0) * rate, 6)


async def get_cost_rates(db: AsyncSession) -> Dict[str, float]:
    """
    Return a dict of {model_id: cost_per_1k_tokens} from DB.
    Falls back to DEFAULT_COST_RATES for any model not in DB.
    """
    try:
        result = await db.execute(select(ModelQuotaPolicy))
        policies = result.scalars().all()
        rates = dict(DEFAULT_COST_RATES)
        for p in policies:
            rates[p.model_id] = p.cost_rate_per_1k_tokens
        return rates
    except Exception as exc:
        logger.warning("get_cost_rates DB error — using defaults: %s", exc)
        return dict(DEFAULT_COST_RATES)


async def get_model_policies(db: AsyncSession) -> list:
    """Return all ModelQuotaPolicy rows, seeding defaults first if empty."""
    await seed_default_rates(db)
    result = await db.execute(
        select(ModelQuotaPolicy).order_by(ModelQuotaPolicy.model_id)
    )
    return result.scalars().all()


async def seed_default_rates(db: AsyncSession) -> None:
    """
    Upsert default cost rates into model_quota_policies.
    Only inserts rows that don't already exist — never overwrites edits.
    """
    try:
        existing_result = await db.execute(select(ModelQuotaPolicy.model_id))
        existing = {row[0] for row in existing_result.fetchall()}

        to_insert = []
        for model_id, rate in DEFAULT_COST_RATES.items():
            if model_id not in existing:
                to_insert.append(ModelQuotaPolicy(
                    model_id=model_id,
                    display_name=DEFAULT_DISPLAY_NAMES.get(model_id, model_id),
                    provider=DEFAULT_PROVIDERS.get(model_id, "azure_openai"),
                    is_enabled=True,
                    cost_rate_per_1k_tokens=rate,
                ))

        if to_insert:
            db.add_all(to_insert)
            await db.commit()
            logger.info("Seeded %d default model cost rates", len(to_insert))
    except Exception as exc:
        logger.warning("seed_default_rates error: %s", exc)
        await db.rollback()


async def get_monthly_cost_summary(
    db: AsyncSession,
    year: int,
    month: int,
    tenant_id: Optional[str] = None,
) -> Dict:
    """
    Aggregate token usage + estimated cost for a given month.
    Returns: { total_tokens, total_cost, by_model: [{model, tokens, cost}] }
    ModelUsage.conversation_id → join Conversation for tenant filtering.
    """
    from sqlalchemy import func, extract
    from app.models.models import Conversation

    rates = await get_cost_rates(db)

    query = (
        select(
            ModelUsage.model,
            func.sum(ModelUsage.total_tokens).label("tokens"),
        )
        .where(
            extract("year",  ModelUsage.created_at) == year,
            extract("month", ModelUsage.created_at) == month,
        )
    )
    if tenant_id:
        query = (
            query
            .join(
                Conversation,
                Conversation.id == ModelUsage.conversation_id,
                isouter=True,
            )
            .where(Conversation.tenant_id == tenant_id)
        )

    query = query.group_by(ModelUsage.model)

    result = await db.execute(query)
    rows = result.fetchall()

    by_model = []
    total_tokens = 0
    total_cost = 0.0

    for model_name, tokens in rows:
        tokens = tokens or 0
        cost = calculate_cost(tokens, model_name, rates)
        total_tokens += tokens
        total_cost += cost
        by_model.append({
            "model": model_name, "tokens": tokens, "cost": round(cost, 4),
        })

    by_model.sort(key=lambda x: x["tokens"], reverse=True)

    return {
        "year": year,
        "month": month,
        "tenant_id": tenant_id,
        "total_tokens": total_tokens,
        "total_cost": round(total_cost, 4),
        "by_model": by_model,
    }


async def list_tenant_summaries(
    db: AsyncSession,
    year: int,
    month: int,
) -> list:
    """
    Per-tenant cost rollup for a given billing month.
    Joins ModelUsage → Conversation to get tenant_id.
    Returns list of { tenant_id, total_tokens, total_cost }.
    """
    from sqlalchemy import func, extract
    from app.models.models import Conversation

    rates = await get_cost_rates(db)

    query = (
        select(
            Conversation.tenant_id,
            ModelUsage.model,
            func.sum(ModelUsage.total_tokens).label("tokens"),
        )
        .join(
            Conversation,
            Conversation.id == ModelUsage.conversation_id,
            isouter=True,
        )
        .where(
            extract("year",  ModelUsage.created_at) == year,
            extract("month", ModelUsage.created_at) == month,
        )
        .group_by(Conversation.tenant_id, ModelUsage.model)
    )

    result = await db.execute(query)
    rows = result.fetchall()

    tenant_map: Dict[str, Dict] = {}
    for tenant_id, model_name, tokens in rows:
        tenant_id = tenant_id or "personal"
        tokens = tokens or 0
        cost = calculate_cost(tokens, model_name, rates)
        if tenant_id not in tenant_map:
            tenant_map[tenant_id] = {
                "tenant_id": tenant_id,
                "total_tokens": 0,
                "total_cost": 0.0,
                "by_model": [],
            }
        tenant_map[tenant_id]["total_tokens"] += tokens
        tenant_map[tenant_id]["total_cost"] += cost
        tenant_map[tenant_id]["by_model"].append({
            "model": model_name, "tokens": tokens, "cost": round(cost, 4),
        })

    summaries = list(tenant_map.values())
    for s in summaries:
        s["total_cost"] = round(s["total_cost"], 4)
    summaries.sort(key=lambda x: x["total_cost"], reverse=True)
    return summaries
