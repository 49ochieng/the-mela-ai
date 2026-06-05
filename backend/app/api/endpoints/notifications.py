"""
Mela AI - Notification Endpoints

CRUD endpoints for user notifications (share invites, budget warnings, etc.)
"""

import logging
from fastapi import APIRouter, Depends, HTTPException, status, Query
from sqlalchemy.ext.asyncio import AsyncSession
from typing import Optional

from app.core.database import get_db
from app.core.security import get_current_user
from app.schemas.auth import UserInfo
import app.services.notification_service as notif_svc

logger = logging.getLogger(__name__)
router = APIRouter()


@router.get("")
async def get_notifications(
    unread_only: bool = Query(False),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    current_user: UserInfo = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Get notifications for the current user."""
    notifications = await notif_svc.get_notifications(
        db, current_user.id, unread_only=unread_only, limit=limit, offset=offset
    )
    return notifications


@router.get("/unread-count")
async def get_unread_count(
    current_user: UserInfo = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Get the count of unread notifications."""
    count = await notif_svc.get_unread_count(db, current_user.id)
    return {"unread_count": count, "count": count}


@router.patch("/{notification_id}/read")
async def mark_notification_read(
    notification_id: str,
    current_user: UserInfo = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Mark a single notification as read."""
    success = await notif_svc.mark_read(db, current_user.id, notification_id)
    if not success:
        raise HTTPException(status_code=404, detail="Notification not found")
    await db.commit()
    return {"status": "ok"}


@router.post("/mark-all-read")
async def mark_all_read(
    current_user: UserInfo = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Mark all notifications as read."""
    count = await notif_svc.mark_all_read(db, current_user.id)
    await db.commit()
    return {"marked": count}


@router.delete("/{notification_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_notification(
    notification_id: str,
    current_user: UserInfo = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Delete a notification."""
    success = await notif_svc.delete_notification(db, current_user.id, notification_id)
    if not success:
        raise HTTPException(status_code=404, detail="Notification not found")
    await db.commit()
