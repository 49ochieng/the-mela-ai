"""
alert_service.py
Zero-blindness ops alerting for Mela AI.
Channels: ACS Email + Microsoft Teams Adaptive Card.
All calls are fire-and-forget — never raises to caller.
Default recipient: edgar.mcochieng@armely.com
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import uuid
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Optional

import httpx

from app.core.config import settings

if TYPE_CHECKING:
    pass

logger = logging.getLogger(__name__)


# ── Dataclasses ───────────────────────────────────────────────

@dataclass
class AiTriage:
    probable_cause: str
    confidence: float
    immediate_mitigation: str
    likely_owner: str
    first_validation_step: str
    generated_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    model_used: str = ""
    human_validation_required: bool = True


@dataclass
class AlertIncident:
    title: str
    severity: str              # critical | warning | info
    code: str
    id: str = field(default_factory=lambda: str(uuid.uuid4()))
    timestamp: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    route: Optional[str] = None
    tenant_id: Optional[str] = None
    error_message: Optional[str] = None
    stack_trace: Optional[str] = None
    worker: Optional[str] = None
    ai_triage: Optional[AiTriage] = None


# ── Fingerprint / cooldown ────────────────────────────────────

def _fingerprint(incident: AlertIncident) -> str:
    raw = f"{incident.code}:{incident.route or ''}:{incident.severity}"
    return "alert:cooldown:" + hashlib.sha256(raw.encode()).hexdigest()[:16]


async def _is_suppressed(fp: str) -> bool:
    try:
        from app.core.redis_client import get_redis
        redis = await get_redis()
        if redis is None:
            return False
        return await redis.exists(fp) == 1
    except Exception as exc:
        logger.warning("alert cooldown Redis check failed — will send: %s", exc)
        return False


async def _set_cooldown(fp: str) -> None:
    try:
        from app.core.redis_client import get_redis
        redis = await get_redis()
        if redis is None:
            return
        await redis.set(fp, "1", ex=settings.ALERT_COOLDOWN_SECONDS)
    except Exception as exc:
        logger.warning("alert cooldown Redis set failed: %s", exc)


# ── Telemetry ─────────────────────────────────────────────────

def _track(channel: str, status: str, incident_id: str) -> None:
    try:
        from opentelemetry import trace
        tracer = trace.get_tracer(__name__)
        with tracer.start_as_current_span("alert_delivery") as span:
            span.set_attribute("alert.channel", channel)
            span.set_attribute("alert.status", status)
            span.set_attribute("alert.incident_id", incident_id)
    except Exception:
        pass


# ── Email body ────────────────────────────────────────────────

def _build_email_body(incident: AlertIncident) -> str:
    stack = ""
    if incident.stack_trace:
        lines = incident.stack_trace.splitlines()
        stack = "\n".join(lines[-10:])

    triage_block = "Not available"
    if incident.ai_triage:
        t = incident.ai_triage
        triage_block = (
            f"Probable cause    : {t.probable_cause}\n"
            f"Confidence        : {t.confidence:.0%}\n"
            f"Mitigation        : {t.immediate_mitigation}\n"
            f"Likely owner      : {t.likely_owner}\n"
            f"First check       : {t.first_validation_step}"
        )

    return f"""[MELA AI ALERT] {incident.severity.upper()} — {incident.title}

Incident ID : {incident.id}
Time        : {incident.timestamp.strftime('%Y-%m-%d %H:%M:%S')} UTC
Code        : {incident.code}
Route       : {incident.route or 'N/A'}
Tenant      : {incident.tenant_id or 'N/A'}
Worker      : {incident.worker or 'N/A'}

ERROR
-----
{incident.error_message or 'No message captured'}

STACK TRACE (last 10 lines)
---------------------------
{stack or 'Not available'}

AI TRIAGE  ⚠ HUMAN VALIDATION REQUIRED
-----------------------------------------
{triage_block}

—
Mela AI Incident Notification
"""


# ── ACS email channel ─────────────────────────────────────────

async def _send_email(incident: AlertIncident) -> bool:
    if not settings.ACS_CONNECTION_STRING:
        logger.warning("ACS_CONNECTION_STRING not configured — skipping email")
        return False

    recipients = list(settings.ALERT_RECIPIENTS)
    if "edgar.mcochieng@armely.com" not in recipients:
        recipients.append("edgar.mcochieng@armely.com")

    message = {
        "senderAddress": settings.ACS_SENDER_ADDRESS,
        "recipients": {"to": [{"address": r} for r in recipients]},
        "content": {
            "subject": f"[MELA AI {incident.severity.upper()}] {incident.title}",
            "plainText": _build_email_body(incident),
        },
    }

    def _blocking_send():
        from azure.communication.email import EmailClient
        client = EmailClient.from_connection_string(settings.ACS_CONNECTION_STRING)
        poller = client.begin_send(message)
        return poller.result()

    for attempt in range(settings.ALERT_MAX_RETRIES):
        try:
            result = await asyncio.to_thread(_blocking_send)
            status_val = result["status"] if isinstance(result, dict) else getattr(result, "status", None)
            if status_val == "Succeeded":
                logger.info("alert ACS email sent: incident=%s", incident.id)
                _track("email", "sent", incident.id)
                return True
            raise RuntimeError(f"ACS status: {status_val}")
        except Exception as exc:
            backoff = settings.ALERT_RETRY_BACKOFF_BASE ** attempt
            logger.warning("alert email attempt %d failed (%.0fs): %s", attempt + 1, backoff, exc)
            try:
                await asyncio.sleep(backoff)
            except Exception:
                pass

    _track("email", "failed", incident.id)
    return False


# ── Teams channel ─────────────────────────────────────────────

def _build_teams_card(incident: AlertIncident) -> dict:
    color = "attention" if incident.severity == "critical" else "warning"
    badge = "🔴 CRITICAL" if incident.severity == "critical" else "🟡 WARNING"

    facts = [
        {"title": "Incident ID", "value": incident.id},
        {"title": "Time (UTC)", "value": incident.timestamp.strftime("%Y-%m-%d %H:%M:%S")},
        {"title": "Code", "value": incident.code},
        {"title": "Route", "value": incident.route or "N/A"},
        {"title": "Tenant", "value": incident.tenant_id or "N/A"},
        {"title": "Worker", "value": incident.worker or "N/A"},
    ]

    triage_facts = []
    if incident.ai_triage:
        t = incident.ai_triage
        triage_facts = [
            {"title": "Probable cause", "value": t.probable_cause},
            {"title": "Confidence", "value": f"{t.confidence:.0%}"},
            {"title": "Mitigation", "value": t.immediate_mitigation},
            {"title": "Owner", "value": t.likely_owner},
            {"title": "First check", "value": t.first_validation_step},
        ]

    body = [
        {"type": "TextBlock", "text": f"{badge} — {incident.title}",
         "weight": "Bolder", "size": "Medium", "color": color},
        {"type": "TextBlock", "text": incident.error_message or "No message",
         "wrap": True, "isSubtle": True},
        {"type": "FactSet", "facts": facts},
    ]

    if triage_facts:
        body += [
            {"type": "TextBlock", "text": "⚠ AI TRIAGE — Human validation required",
             "weight": "Bolder", "separator": True},
            {"type": "FactSet", "facts": triage_facts},
        ]

    return {
        "type": "message",
        "attachments": [{
            "contentType": "application/vnd.microsoft.card.adaptive",
            "content": {
                "$schema": "http://adaptivecards.io/schemas/adaptive-card.json",
                "type": "AdaptiveCard",
                "version": "1.4",
                "body": body,
            },
        }],
    }


async def _send_teams(incident: AlertIncident) -> bool:
    if not settings.TEAMS_WEBHOOK_URL:
        logger.warning("TEAMS_WEBHOOK_URL not configured — skipping Teams")
        return False
    try:
        async with httpx.AsyncClient(timeout=15) as client:
            r = await client.post(settings.TEAMS_WEBHOOK_URL, json=_build_teams_card(incident))
            r.raise_for_status()
        logger.info("alert Teams card sent: incident=%s", incident.id)
        _track("teams", "sent", incident.id)
        return True
    except Exception as exc:
        logger.error("alert Teams send failed: %s", exc)
        _track("teams", "failed", incident.id)
        return False


# ── Dead-letter fallback ──────────────────────────────────────

def _write_deadletter(incident: AlertIncident) -> None:
    try:
        record = asdict(incident)
        record["timestamp"] = incident.timestamp.isoformat()
        if incident.ai_triage:
            record["ai_triage"]["generated_at"] = incident.ai_triage.generated_at.isoformat()
        with open("/tmp/alert_deadletter.jsonl", "a") as f:
            f.write(json.dumps(record) + "\n")
        logger.error("alert.deadletter written for incident=%s", incident.id)
        _track("deadletter", "written", incident.id)
    except Exception as exc:
        logger.critical("alert deadletter write failed: %s", exc)


# ── Main entry point ──────────────────────────────────────────

async def send_alert(incident: AlertIncident) -> None:
    """Fire-and-forget. Never raises. Always logs outcome."""
    try:
        fp = _fingerprint(incident)

        if incident.severity != "critical" and await _is_suppressed(fp):
            logger.info("alert suppressed (cooldown): code=%s", incident.code)
            _track("all", "suppressed", incident.id)
            return

        if incident.severity == "critical" and incident.ai_triage is None:
            try:
                from app.services.ai_triage import generate_triage
                incident.ai_triage = await generate_triage(incident)
            except ImportError:
                pass
            except Exception as exc:
                logger.warning("AI triage failed — sending without: %s", exc)

        channels = settings.ALERT_CHANNELS
        email_ok = await _send_email(incident) if "email" in channels else False
        teams_ok = await _send_teams(incident) if "teams" in channels else False

        if not email_ok and not teams_ok and settings.TEAMS_WEBHOOK_URL:
            logger.warning("email failed — emergency Teams fallback: %s", incident.id)
            teams_ok = await _send_teams(incident)
            _track("teams", "fallback", incident.id)

        if not email_ok and not teams_ok:
            _write_deadletter(incident)
            return

        await _set_cooldown(fp)

    except Exception as exc:
        logger.critical("send_alert unhandled: %s", exc, exc_info=True)
