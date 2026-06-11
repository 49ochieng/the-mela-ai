"""Health + readiness."""
from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from ..database import get_session

router = APIRouter()


@router.get("/health")
async def health() -> dict:
    return {"status": "ok"}


@router.get("/ready")
async def ready(session: AsyncSession = Depends(get_session)) -> dict:
    await session.execute(text("SELECT 1"))
    return {"status": "ready"}
