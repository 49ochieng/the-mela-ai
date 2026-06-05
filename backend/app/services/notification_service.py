"""
Mela AI - Notification Service

In-app + email notifications for shares, budget alerts, and system events.
"""

import logging
from typing import Optional, List
from datetime import datetime
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func, update as sa_update, delete as sa_delete

from app.models.models import Notification, NotificationType, User

logger = logging.getLogger(__name__)


async def create_notification(
    db: AsyncSession,
    user_id: str,
    type: NotificationType,
    title: str,
    message: str,
    link_type: Optional[str] = None,
    link_id: Optional[str] = None,
    actor_id: Optional[str] = None,
    send_email: bool = False,
) -> Notification:
    """Create an in-app notification for a user."""
    notif = Notification(
        user_id=user_id,
        type=type,
        title=title,
        message=message,
        link_type=link_type,
        link_id=link_id,
        actor_id=actor_id,
        is_email_sent=False,
    )
    db.add(notif)

    if send_email:
        try:
            await _send_email_notification(db, user_id, title, message)
            notif.is_email_sent = True
        except Exception as exc:
            logger.warning("Failed to send email notification: %s", exc)

    await db.flush()
    return notif


async def get_notifications(
    db: AsyncSession,
    user_id: str,
    unread_only: bool = False,
    limit: int = 50,
    offset: int = 0,
) -> List[dict]:
    """Get notifications for a user."""
    query = (
        select(Notification)
        .where(Notification.user_id == user_id)
        .order_by(Notification.created_at.desc())
        .limit(limit)
        .offset(offset)
    )
    if unread_only:
        query = query.where(Notification.is_read == False)

    result = await db.execute(query)
    notifications = result.scalars().all()

    items = []
    for n in notifications:
        actor_name = None
        if n.actor_id:
            actor = await db.scalar(select(User.name).where(User.id == n.actor_id))
            actor_name = actor

        items.append({
            "id": n.id,
            "type": n.type.value,
            "title": n.title,
            "message": n.message,
            "link_type": n.link_type,
            "link_id": n.link_id,
            "actor_name": actor_name,
            "is_read": n.is_read,
            "created_at": n.created_at.isoformat(),
        })

    return items


async def get_unread_count(db: AsyncSession, user_id: str) -> int:
    """Get count of unread notifications for a user."""
    result = await db.scalar(
        select(func.count(Notification.id))
        .where(Notification.user_id == user_id, Notification.is_read == False)
    )
    return result or 0


async def mark_read(db: AsyncSession, user_id: str, notification_id: str) -> bool:
    """Mark a single notification as read."""
    result = await db.execute(
        sa_update(Notification)
        .where(Notification.id == notification_id, Notification.user_id == user_id)
        .values(is_read=True)
    )
    await db.flush()
    return result.rowcount > 0


async def mark_all_read(db: AsyncSession, user_id: str) -> int:
    """Mark all notifications as read for a user. Returns count marked."""
    result = await db.execute(
        sa_update(Notification)
        .where(Notification.user_id == user_id, Notification.is_read == False)
        .values(is_read=True)
    )
    await db.flush()
    return result.rowcount


async def delete_notification(db: AsyncSession, user_id: str, notification_id: str) -> bool:
    """Delete a notification."""
    result = await db.execute(
        sa_delete(Notification)
        .where(Notification.id == notification_id, Notification.user_id == user_id)
    )
    await db.flush()
    return result.rowcount > 0


# ── Share notifications ──────────────────────────────────────────────────────

async def notify_share_invite(
    db: AsyncSession,
    invitee_user_id: str,
    inviter_name: str,
    resource_type: str,
    resource_name: str,
    resource_id: str,
) -> None:
    """Create a notification when someone is invited to a project/chat."""
    await create_notification(
        db=db,
        user_id=invitee_user_id,
        type=NotificationType.SHARE_INVITE,
        title=f"Shared with you: {resource_name}",
        message=f"{inviter_name} shared a {resource_type} with you.",
        link_type=resource_type,
        link_id=resource_id,
        send_email=True,
    )


async def notify_share_accepted(
    db: AsyncSession,
    owner_user_id: str,
    accepter_name: str,
    resource_type: str,
    resource_name: str,
    resource_id: str,
) -> None:
    """Notify owner when an invite is accepted."""
    await create_notification(
        db=db,
        user_id=owner_user_id,
        type=NotificationType.SHARE_ACCEPTED,
        title=f"{accepter_name} accepted your invite",
        message=f"{accepter_name} joined your {resource_type} '{resource_name}'.",
        link_type=resource_type,
        link_id=resource_id,
    )


# ── Budget notifications ─────────────────────────────────────────────────────

async def notify_budget_warning(
    db: AsyncSession,
    user_id: str,
    usage_pct: int,
    budget_type: str,
) -> None:
    """Warn user approaching their budget limit."""
    await create_notification(
        db=db,
        user_id=user_id,
        type=NotificationType.BUDGET_WARNING,
        title=f"Budget alert: {min(usage_pct, 999)}% of {budget_type} budget used",
        message=f"You've used {min(usage_pct, 999)}% of your {budget_type} budget. "
                f"Consider reducing usage to avoid a hard stop.",
    )


async def notify_budget_exceeded(
    db: AsyncSession,
    user_id: str,
    budget_type: str,
) -> None:
    """Notify user that their budget has been exceeded."""
    await create_notification(
        db=db,
        user_id=user_id,
        type=NotificationType.BUDGET_EXCEEDED,
        title=f"Budget exceeded: {budget_type}",
        message=f"Your {budget_type} budget has been exceeded. "
                f"Requests may be blocked until the next period.",
    )


# ── Email sending via Microsoft Graph app-only token ────────────────────────

async def _send_email_notification(
    db: AsyncSession,
    user_id: str,
    subject: str,
    body: str,
) -> None:
    """Send an email notification via Microsoft Graph (app-only flow).

    Uses the configured GRAPH_SENDER_EMAIL as the From address and sends
    to the target user's registered email address.  Fails silently — in-app
    notification is always created regardless of email delivery status.
    """
    user = await db.scalar(select(User).where(User.id == user_id))
    if not (user and user.email):
        logger.debug("Email notification skipped: no email for user %s", user_id)
        return

    logger.info(
        "Sending email notification to %s | Subject: %s", user.email, subject
    )

    try:
        from app.core.config import settings
        from app.services.graph_service import graph_service

        if not graph_service:
            logger.warning("Graph service unavailable — email notification not sent")
            return

        html_body = (
            f"<html><body>"
            f"<p>{body}</p>"
            f"<hr/>"
            f"<p style='color:#888;font-size:12px;'>"
            f"This is an automated notification from {settings.APP_NAME}."
            f"</p>"
            f"</body></html>"
        )

        sender = getattr(settings, "GRAPH_SENDER_EMAIL", None)
        if not sender:
            logger.warning("GRAPH_SENDER_EMAIL not set — email notification skipped")
            return

        await graph_service.send_email_app_only(
            sender_email=sender,
            to=[user.email],
            subject=subject,
            body=html_body,
            is_html=True,
        )
        logger.info("Email notification sent to %s", user.email)

    except Exception as exc:
        # Never let email failure block in-app notification creation
        logger.warning(
            "Email notification failed for user %s (%s): %s",
            user_id, getattr(user, "email", "?"), exc,
        )
