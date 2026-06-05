"""
Mela AI - Budget / Governance Endpoints

Endpoints for users to view their budget status and for admins to manage budgets.
"""

import logging
from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
from typing import Optional
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.core.security import get_current_user
from app.schemas.auth import UserInfo
import app.services.budget_service as budget_svc

logger = logging.getLogger(__name__)
router = APIRouter()


class SetBudgetRequest(BaseModel):
    user_id: str
    token_budget: Optional[int] = None
    cost_budget: Optional[float] = None
    period: str = "monthly"
    hard_stop: bool = False
    token_warning_pct: int = 80
    cost_warning_pct: int = 80


@router.get("/me")
async def get_my_budget(
    current_user: UserInfo = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Get the current user's budget status and usage summary."""
    summary = await budget_svc.get_user_budget_summary(
        db, current_user.id, tenant_id=current_user.tenant_id
    )
    if not summary:
        return {"has_budget": False}
    return {"has_budget": True, **summary}


@router.get("/check")
async def check_my_budget(
    current_user: UserInfo = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Quick budget check (called before sending messages)."""
    result = await budget_svc.check_budget(
        db, current_user.id, tenant_id=current_user.tenant_id
    )
    return result.to_dict()


@router.post("/admin/set", status_code=status.HTTP_200_OK)
async def set_user_budget(
    body: SetBudgetRequest,
    current_user: UserInfo = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Admin: set or update a user's budget. Requires admin role."""
    if not any(r.lower() == "admin" for r in (current_user.roles or [])):
        raise HTTPException(status_code=403, detail="Admin role required")

    budget = await budget_svc.set_user_budget(
        db=db,
        user_id=body.user_id,
        admin_id=current_user.id,
        token_budget=body.token_budget,
        cost_budget=body.cost_budget,
        period=body.period,
        hard_stop=body.hard_stop,
        token_warning_pct=body.token_warning_pct,
        cost_warning_pct=body.cost_warning_pct,
    )
    await db.commit()
    return {"status": "ok", "budget_id": str(budget.id)}
