"""
Mela AI - Model Access Control Service

Filters available models based on per-user and per-role access rules.
Falls back to the global ModelRanking list when no restrictions are set.
"""

import logging
from typing import List, Optional
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select

from app.models.models import UserModelAccess, ModelRanking

logger = logging.getLogger(__name__)


async def get_allowed_models(
    db: AsyncSession,
    user_id: str,
    roles: List[str],
) -> List[ModelRanking]:
    """Return the list of models the user is allowed to use.

    Logic:
    1. If there are UserModelAccess rows for this user (or any of their roles),
       filter to only allowed models.
    2. If no access rows exist at all, return all enabled models (no restriction).
    """
    # Check for user-specific rules
    result = await db.execute(
        select(UserModelAccess).where(UserModelAccess.user_id == user_id)
    )
    user_rules = result.scalars().all()

    # Check for role-based rules
    role_rules = []
    if roles:
        result = await db.execute(
            select(UserModelAccess).where(UserModelAccess.role.in_(roles))
        )
        role_rules = result.scalars().all()

    # Combine: user-specific overrides role-based
    all_rules = {r.model_id: r for r in role_rules}  # role first
    for r in user_rules:
        all_rules[r.model_id] = r  # user overrides

    # If no rules, return all enabled models
    if not all_rules:
        result = await db.execute(
            select(ModelRanking)
            .where(ModelRanking.is_enabled == True)
            .order_by(ModelRanking.rank)
        )
        return result.scalars().all()

    # Filter to allowed model IDs
    allowed_ids = {mid for mid, rule in all_rules.items() if rule.is_allowed}
    if not allowed_ids:
        # All rules are deny — return nothing
        return []

    result = await db.execute(
        select(ModelRanking)
        .where(
            ModelRanking.is_enabled == True,
            ModelRanking.model_id.in_(allowed_ids),
        )
        .order_by(ModelRanking.rank)
    )
    return result.scalars().all()


async def is_model_allowed(
    db: AsyncSession,
    user_id: str,
    roles: List[str],
    model_id: str,
) -> bool:
    """Quick check: can this user use this specific model?

    UserModelAccess rows are the authoritative gate.  ModelRanking is for
    ordering / pricing only — a model absent from ModelRanking is NOT blocked.

    Rules:
    - If no UserModelAccess rows exist for user+roles → allow everything.
    - If UserModelAccess rows exist → the model must appear as is_allowed=True.
    - A model not in ModelRanking but not explicitly denied is allowed.
    """
    from sqlalchemy import func

    # Fast path: no access rules at all → open
    access_count = (await db.execute(select(func.count()).select_from(UserModelAccess))).scalar_one()
    if access_count == 0:
        return True

    # Check user-specific rules
    result = await db.execute(
        select(UserModelAccess).where(
            UserModelAccess.user_id == user_id,
            UserModelAccess.model_id == model_id,
        )
    )
    user_rule = result.scalar_one_or_none()
    if user_rule is not None:
        return bool(user_rule.is_allowed)

    # Check role-based rules
    if roles:
        result = await db.execute(
            select(UserModelAccess).where(
                UserModelAccess.role.in_(roles),
                UserModelAccess.model_id == model_id,
            )
        )
        role_rule = result.scalar_one_or_none()
        if role_rule is not None:
            return bool(role_rule.is_allowed)

    # Model not mentioned in any rule → allowed by default
    return True


async def set_model_access(
    db: AsyncSession,
    model_id: str,
    is_allowed: bool,
    user_id: Optional[str] = None,
    role: Optional[str] = None,
    set_by: Optional[str] = None,
) -> UserModelAccess:
    """Admin: set or update a model access rule."""
    from datetime import datetime

    # Find existing rule
    query = select(UserModelAccess).where(UserModelAccess.model_id == model_id)
    if user_id:
        query = query.where(UserModelAccess.user_id == user_id)
    elif role:
        query = query.where(UserModelAccess.role == role)

    result = await db.execute(query)
    rule = result.scalar_one_or_none()

    if rule:
        rule.is_allowed = is_allowed
    else:
        rule = UserModelAccess(
            user_id=user_id,
            role=role,
            model_id=model_id,
            is_allowed=is_allowed,
            created_by=set_by or "system",
        )
        db.add(rule)

    await db.flush()
    return rule
