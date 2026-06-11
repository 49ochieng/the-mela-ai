"""Multi-cadence scheduler. Run with: python -m app.scheduler.scheduler

Per-user cadence honoring User.timezone:
  Day:   07, 09, 11, 13, 15, 17  (every 2h, 7am-5pm local)
  Night: 20, 23, 03               (3 off-hours sweeps)

Default timezone is America/Chicago (CT). Each user can override via their
profile (e.g. America/New_York for ET, America/Los_Angeles for PT).

The job runs every minute UTC and, for each active user, computes whether
:00 in their local timezone matches a cadence hour.

A 50-minute dedupe window prevents double-firing.
"""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from sqlalchemy import select

from ..database import session_scope
from ..enums import ScanStatus, ScanType
from ..logging_config import setup_logging
from ..models import GraphConnection, ScanRun, ScanSettings, User
from ..services.queue.queue import get_queue
from ..workers.account_deleter import run_due_deletions

logger = logging.getLogger(__name__)

DEFAULT_TZ = ZoneInfo("America/Chicago")
SCAN_HOURS_LOCAL: list[int] = [7, 9, 11, 13, 15, 17, 20, 23, 3]
DEDUPE_WINDOW_MIN = 50
WARMUP_STALE_HOURS = 2


def _user_tz(name: str | None) -> ZoneInfo:
    if not name:
        return DEFAULT_TZ
    try:
        return ZoneInfo(name)
    except Exception:  # noqa: BLE001
        return DEFAULT_TZ


def _now_local(tz: ZoneInfo | None = None) -> datetime:
    return datetime.now(tz or DEFAULT_TZ)


def _next_due_local(now: datetime | None = None, tz: ZoneInfo | None = None) -> datetime:
    """Return the next cadence datetime in the given (or default) timezone."""
    z = tz or DEFAULT_TZ
    n = now or _now_local(z)
    today = n.replace(minute=0, second=0, microsecond=0)
    for h in sorted(SCAN_HOURS_LOCAL):
        cand = today.replace(hour=h)
        if cand > n:
            return cand
    return (today + timedelta(days=1)).replace(hour=min(SCAN_HOURS_LOCAL))


async def _enqueue_scan_for_user(session, *, tenant_id: str, user_id: str, trigger: str) -> str | None:
    cutoff = datetime.utcnow() - timedelta(minutes=DEDUPE_WINDOW_MIN)
    recent = (await session.execute(
        select(ScanRun).where(
            ScanRun.tenant_id == tenant_id,
            ScanRun.user_id == user_id,
            ScanRun.created_at >= cutoff,
        )
    )).scalars().first()
    if recent:
        return None
    sr = ScanRun(
        tenant_id=tenant_id,
        user_id=user_id,
        scan_type=ScanType.ALL.value,
        source_scope={"trigger": trigger},
        status=ScanStatus.PENDING.value,
    )
    session.add(sr)
    await session.flush()
    await get_queue().enqueue({"type": "scan", "scan_run_id": sr.id})
    logger.info("Enqueued scan trigger=%s user=%s scan_run_id=%s", trigger, user_id, sr.id)
    return sr.id


async def _active_users(session) -> list[tuple[str, str, str]]:
    """Return [(tenant_id, user_id, timezone_name), ...] for active users."""
    rows = (await session.execute(
        select(ScanSettings, GraphConnection, User)
        .join(
            GraphConnection,
            (GraphConnection.user_id == ScanSettings.user_id)
            & (GraphConnection.tenant_id == ScanSettings.tenant_id)
            & (GraphConnection.provider == "microsoft"),
        )
        .join(User, User.id == ScanSettings.user_id)
        .where(GraphConnection.status == "connected")
    )).all()
    out: list[tuple[str, str, str]] = []
    for settings, _conn, user in rows:
        if not (settings.email_scan_enabled or settings.teams_scan_enabled):
            continue
        out.append((settings.tenant_id, settings.user_id, user.timezone or "America/Chicago"))
    return out


async def _tick() -> None:
    """Runs every minute UTC. For each active user, checks whether :00 in
    that user's local timezone falls on a cadence hour."""
    now_utc = datetime.now(timezone.utc)
    if now_utc.second != 0:
        # APScheduler cron fires once per second=0 already, but guard anyway
        pass
    async with session_scope() as session:
        users = await _active_users(session)
        fired: list[str] = []
        for tenant_id, user_id, tz_name in users:
            tz = _user_tz(tz_name)
            local = now_utc.astimezone(tz)
            if local.minute != 0 or local.hour not in SCAN_HOURS_LOCAL:
                continue
            try:
                sr_id = await _enqueue_scan_for_user(
                    session, tenant_id=tenant_id, user_id=user_id, trigger="cadence",
                )
                if sr_id:
                    fired.append(f"{user_id[:8]}@{tz_name}:{local.hour:02d}")
            except Exception as exc:  # noqa: BLE001
                logger.exception("Cadence enqueue failed user=%s: %s", user_id, exc)
        if fired:
            logger.info("Cadence tick — enqueued %d scan(s): %s", len(fired), ", ".join(fired))


async def warmup_pass() -> None:
    """Enqueue a scan for any active user whose last ScanRun is >2h ago.
    Called from API lifespan so a fresh boot doesn't wait for the next slot."""
    cutoff = datetime.utcnow() - timedelta(hours=WARMUP_STALE_HOURS)
    async with session_scope() as session:
        users = await _active_users(session)
        for tenant_id, user_id, _tz_name in users:
            recent = (await session.execute(
                select(ScanRun).where(
                    ScanRun.tenant_id == tenant_id,
                    ScanRun.user_id == user_id,
                    ScanRun.created_at >= cutoff,
                )
            )).scalars().first()
            if recent:
                continue
            try:
                await _enqueue_scan_for_user(
                    session, tenant_id=tenant_id, user_id=user_id, trigger="warmup",
                )
            except Exception as exc:  # noqa: BLE001
                logger.exception("Warmup enqueue failed user=%s: %s", user_id, exc)


async def _account_deleter_tick() -> None:
    """Daily wrapper around the GDPR hard-delete pass."""
    try:
        n = await run_due_deletions()
        if n:
            logger.info("account_deleter: hard-deleted %d user(s)", n)
    except Exception:  # noqa: BLE001
        logger.exception("account_deleter tick failed")


async def main() -> None:
    setup_logging()
    sched = AsyncIOScheduler()
    sched.add_job(_tick, "cron", second=0)
    # Daily GDPR hard-delete pass at 03:00 UTC.
    sched.add_job(_account_deleter_tick, "cron", hour=3, minute=0)
    sched.start()
    logger.info(
        "Scheduler started — per-user cadence (default America/Chicago) hours: %s",
        ", ".join(f"{h:02d}" for h in SCAN_HOURS_LOCAL),
    )
    try:
        await warmup_pass()
    except Exception as exc:  # noqa: BLE001
        logger.exception("Initial warmup pass failed: %s", exc)
    try:
        while True:
            await asyncio.sleep(3600)
    except (KeyboardInterrupt, SystemExit):
        pass


if __name__ == "__main__":
    asyncio.run(main())
