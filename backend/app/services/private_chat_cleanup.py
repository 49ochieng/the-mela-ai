"""
Mela AI - Private Chat Auto-Deletion Background Task

Runs hourly. Hard-deletes any Conversation where is_private=True
and private_expires_at has passed (20-day governance retention window).
"""

import asyncio
import logging
from datetime import datetime

from sqlalchemy import delete

logger = logging.getLogger(__name__)

CLEANUP_INTERVAL_SECONDS = 3600  # Run every hour


def _fire_ops_alert(message: str, route: str) -> None:
    """Best-effort alert path for cleanup failures."""
    try:
        import asyncio as _asyncio
        from app.services.alert_service import send_alert, AlertIncident
        incident = AlertIncident(
            title=f"PrivateChatCleanupError: {message[:120]}",
            severity="warning",
            code="PRIVATE_CHAT_CLEANUP_ERROR",
            route=route,
            error_message=message[:500],
        )
        try:
            loop = _asyncio.get_running_loop()
            loop.create_task(send_alert(incident))
        except RuntimeError:
            _asyncio.run(send_alert(incident))
    except Exception:
        pass


async def delete_expired_private_conversations() -> int:
    """Hard-delete expired private conversations. Returns count deleted."""
    from app.core.database import async_session_maker, db_available
    from app.models.models import Conversation

    if not db_available:
        # No DB — purge expired entries from the in-memory fallback store
        from app.services.chat_service import _in_memory_conversations, _in_memory_messages
        now = datetime.utcnow()
        expired = [
            k for k, v in list(_in_memory_conversations.items())
            if v.get("is_private") and v.get("private_expires_at") and v["private_expires_at"] <= now
        ]
        for k in expired:
            _in_memory_conversations.pop(k, None)
            _in_memory_messages.pop(k, None)
        if expired:
            logger.info(f"Private chat cleanup (in-memory): removed {len(expired)} expired conversation(s)")
        return len(expired)

    try:
        async with async_session_maker() as session:
            now = datetime.utcnow()
            result = await session.execute(
                delete(Conversation).where(
                    Conversation.is_private == True,  # noqa: E712
                    Conversation.private_expires_at <= now,
                )
            )
            deleted = result.rowcount or 0
            await session.commit()
            if deleted:
                logger.info(f"Private chat cleanup: deleted {deleted} expired conversation(s)")
            return deleted
    except Exception as exc:
        logger.error(f"Private chat cleanup failed: {exc}")
        _fire_ops_alert(
            message=f"delete_expired_private_conversations failed: {exc}",
            route="background:private_chat_cleanup.delete",
        )
        return 0


async def start_cleanup_task() -> None:
    """Infinite loop that periodically deletes expired private conversations."""
    # Brief initial delay so the app is fully started before first run
    await asyncio.sleep(60)
    while True:
        try:
            await delete_expired_private_conversations()
        except Exception as exc:
            logger.error(f"Unexpected error in private chat cleanup loop: {exc}")
            _fire_ops_alert(
                message=f"start_cleanup_task loop failure: {exc}",
                route="background:private_chat_cleanup.loop",
            )
        await asyncio.sleep(CLEANUP_INTERVAL_SECONDS)
