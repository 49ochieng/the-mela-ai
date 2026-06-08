"""
test_alert.py — prove the ops-alert pipeline end to end.

What it does
------------
1. Forces a **circuit-breaker trip** for a throwaway worker by recording
   3 failures inside the failure window, and asserts the breaker moved to
   OPEN — this is the real production trigger that fires a critical alert.
2. Fires the corresponding BREAKER_OPEN alert through ``send_alert`` (the
   same incident the breaker builds) and waits for delivery.
3. Verifies an ``alert_events`` row was persisted for that incident and
   prints the channels that were attempted (email / Teams) so you can see
   whether the Teams Adaptive Card / ACS email actually went out.

Run from the backend directory so settings + env load correctly:

    cd backend
    python ../scripts/test_alert.py

Requires the same env the app uses (ACS_CONNECTION_STRING / TEAMS_WEBHOOK_URL
for real delivery; DATABASE_URL for the alert_events check). With Teams/ACS
configured this posts a real Adaptive Card to the configured webhook.
"""

from __future__ import annotations

import asyncio
import os
import sys
import uuid

# Windows consoles default to cp1252 — force UTF-8 so output never crashes.
try:
    sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[attr-defined]
except Exception:
    pass

# Allow running from repo root or backend/.
_HERE = os.path.dirname(os.path.abspath(__file__))
_BACKEND = os.path.join(os.path.dirname(_HERE), "backend")
if os.path.isdir(_BACKEND) and _BACKEND not in sys.path:
    sys.path.insert(0, _BACKEND)


async def main() -> int:
    from app.core.config import settings
    from app.orchestration.breaker import (
        BreakerState,
        CircuitBreaker,
        InMemoryBreakerStore,
    )
    from app.services.alert_service import AlertIncident, send_alert

    worker_id = f"test-worker-demo-{uuid.uuid4().hex[:6]}"
    print("== Mela alert pipeline test ==")
    print(f"worker_id            : {worker_id}")
    print(f"ACS configured       : {bool(settings.ACS_CONNECTION_STRING)}")
    print(f"Teams webhook set    : {bool(settings.TEAMS_WEBHOOK_URL)}")
    print(f"REDIS_URL set        : {bool(settings.REDIS_URL)}")
    print(f"ALERT_CHANNELS       : {settings.ALERT_CHANNELS}")
    print()

    # ── 1. Trip a circuit breaker (the real trigger) ───────────────────────
    # Use an isolated store/breaker so we don't pollute the process singleton.
    breaker = CircuitBreaker(InMemoryBreakerStore())
    threshold = breaker._config.failure_threshold
    print(f"[1] Recording {threshold} failures to trip the breaker...")
    for i in range(threshold):
        await breaker.record_failure(worker_id)
    snap = await breaker.snapshot(worker_id)
    tripped = snap.state == BreakerState.OPEN
    print(f"    breaker state = {snap.state.value} "
          f"(failures={snap.failure_count}) -> "
          f"{'OPEN (tripped) [OK]' if tripped else 'NOT tripped [FAIL]'}")
    if not tripped:
        print("    FAIL: breaker did not trip as expected")
        return 1

    # ── 2. Fire the BREAKER_OPEN alert (same shape breaker.py builds) ──────
    incident = AlertIncident(
        title=f"BREAKER_OPEN: worker={worker_id}",
        severity="critical",
        code="BREAKER_OPEN",
        worker=worker_id,
        error_message=(
            f"Circuit breaker tripped OPEN for worker={worker_id} "
            f"({snap.failure_count} failures) — alert pipeline self-test"
        ),
    )
    print(f"\n[2] Firing critical alert incident_id={incident.id}...")
    await send_alert(incident)
    # send_alert is fire-and-forget; give async channel sends a moment.
    await asyncio.sleep(2.0)

    # ── 3. Verify persistence + report channels ────────────────────────────
    print("\n[3] Checking alert_events for the persisted row...")
    try:
        from sqlalchemy import select
        from app.core.database import async_session_maker
        from app.models.models import AlertEvent
        async with async_session_maker() as db:
            row = (
                await db.execute(
                    select(AlertEvent).where(
                        AlertEvent.incident_id == incident.id
                    )
                )
            ).scalar_one_or_none()
            total = (
                await db.execute(select(AlertEvent))
            ).scalars().all()
        if row is None:
            print("    FAIL: no alert_events row found for this incident")
            print(f"    (total alert_events rows in DB: {len(total)})")
            return 1
        print("    alert_events row FOUND [OK]")
        print(f"    severity          : {row.severity}")
        print(f"    code              : {row.code}")
        print(f"    channels_attempted: {row.channels_attempted}")
        print(f"    total rows in DB  : {len(total)}")
        ch = row.channels_attempted or {}
        if ch.get("teams"):
            print("    Teams Adaptive Card delivered [OK] "
                  "(check the configured Teams channel)")
        elif settings.TEAMS_WEBHOOK_URL:
            print("    Teams attempted but not confirmed delivered — "
                  "check webhook reachability / logs above")
        if ch.get("email"):
            print("    ACS email delivered [OK]")
    except Exception as exc:
        print(f"    ERROR querying alert_events: {exc}")
        print("    (Ensure DATABASE_URL points at a reachable DB and the "
              "alert_events table exists — run init_db / alembic.)")
        return 1

    print("\n== PASS: breaker tripped, alert fired, alert_events recorded ==")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
