"""Tests for the new ACS+Teams ops alert pipeline."""
from __future__ import annotations

import asyncio
import json
import os
import tempfile
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.services.alert_service import (
    AiTriage,
    AlertIncident,
    _build_email_body,
    _build_teams_card,
    _fingerprint,
    send_alert,
)


def _incident(**kw) -> AlertIncident:
    base = dict(
        title="x", severity="critical", code="TEST_CODE",
        route="GET /x", error_message="boom",
    )
    base.update(kw)
    return AlertIncident(**base)


# ── helpers ─────────────────────────────────────────────────────────────

def _settings_patch(
    *, acs="cs", teams="https://teams.example/webhook",
    channels=("email", "teams"), cooldown=300, recipients=("ops@a.com",),
):
    p = patch.multiple(
        "app.core.config.settings",
        ACS_CONNECTION_STRING=acs,
        ACS_SENDER_ADDRESS="DoNotReply@armely.com",
        TEAMS_WEBHOOK_URL=teams,
        ALERT_CHANNELS=list(channels),
        ALERT_COOLDOWN_SECONDS=cooldown,
        ALERT_MAX_RETRIES=1,
        ALERT_RETRY_BACKOFF_BASE=1.0,
        ALERT_RECIPIENTS=list(recipients),
        ALERT_CONFIDENCE_THRESHOLD=0.6,
    )
    return p


# ── 1. ACS email sends successfully ─────────────────────────────────────

@pytest.mark.asyncio
async def test_acs_email_sends_when_configured():
    with _settings_patch(teams=""):
        with patch("app.services.alert_service._is_suppressed", AsyncMock(return_value=False)), \
             patch("app.services.alert_service._set_cooldown", AsyncMock()), \
             patch("app.services.alert_service.asyncio.to_thread",
                   AsyncMock(return_value={"status": "Succeeded"})):
            await send_alert(_incident(severity="warning"))


# ── 2. ACS email skipped if no connection string ────────────────────────

@pytest.mark.asyncio
async def test_acs_email_skipped_when_not_configured():
    with _settings_patch(acs="", teams=""):
        with patch("app.services.alert_service._is_suppressed", AsyncMock(return_value=False)), \
             patch("app.services.alert_service._write_deadletter") as dl:
            await send_alert(_incident(severity="warning"))
            # both channels disabled -> deadletter
            dl.assert_called_once()


# ── 3. Teams sends ──────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_teams_sends_when_configured():
    fake_client = MagicMock()
    fake_client.__aenter__ = AsyncMock(return_value=fake_client)
    fake_client.__aexit__ = AsyncMock(return_value=False)
    fake_resp = MagicMock()
    fake_resp.raise_for_status = MagicMock()
    fake_client.post = AsyncMock(return_value=fake_resp)

    with _settings_patch(acs=""):
        with patch("app.services.alert_service._is_suppressed", AsyncMock(return_value=False)), \
             patch("app.services.alert_service._set_cooldown", AsyncMock()), \
             patch("app.services.alert_service.httpx.AsyncClient", return_value=fake_client):
            await send_alert(_incident(severity="warning"))
            fake_client.post.assert_called_once()


# ── 4. Teams skipped if no webhook ──────────────────────────────────────

@pytest.mark.asyncio
async def test_teams_skipped_when_not_configured():
    with _settings_patch(acs="", teams=""):
        with patch("app.services.alert_service._is_suppressed", AsyncMock(return_value=False)), \
             patch("app.services.alert_service._write_deadletter") as dl:
            await send_alert(_incident(severity="warning"))
            dl.assert_called_once()


# ── 5. Cooldown suppresses non-critical ─────────────────────────────────

@pytest.mark.asyncio
async def test_cooldown_suppresses_non_critical():
    with _settings_patch():
        with patch("app.services.alert_service._is_suppressed", AsyncMock(return_value=True)), \
             patch("app.services.alert_service._send_email") as se, \
             patch("app.services.alert_service._send_teams") as st:
            await send_alert(_incident(severity="warning"))
            se.assert_not_called()
            st.assert_not_called()


# ── 6. Critical bypasses cooldown ───────────────────────────────────────

@pytest.mark.asyncio
async def test_critical_bypasses_cooldown():
    with _settings_patch():
        with patch("app.services.alert_service._is_suppressed", AsyncMock(return_value=True)), \
             patch("app.services.alert_service._set_cooldown", AsyncMock()), \
             patch("app.services.alert_service._send_email", AsyncMock(return_value=True)) as se, \
             patch("app.services.alert_service._send_teams", AsyncMock(return_value=True)), \
             patch("app.services.ai_triage.generate_triage", AsyncMock(return_value=None)):
            await send_alert(_incident(severity="critical"))
            se.assert_called_once()


# ── 7. Both channels fail -> deadletter ─────────────────────────────────

@pytest.mark.asyncio
async def test_both_channels_fail_writes_deadletter(tmp_path, monkeypatch):
    dlpath = tmp_path / "dl.jsonl"
    real_open = open

    def fake_open(path, *a, **kw):
        if path == "/tmp/alert_deadletter.jsonl":
            return real_open(str(dlpath), *a, **kw)
        return real_open(path, *a, **kw)

    with _settings_patch(teams=""):
        with patch("app.services.alert_service._is_suppressed", AsyncMock(return_value=False)), \
             patch("app.services.alert_service._send_email", AsyncMock(return_value=False)), \
             patch("app.services.alert_service._send_teams", AsyncMock(return_value=False)), \
             patch("builtins.open", fake_open):
            await send_alert(_incident(severity="warning"))
    assert dlpath.exists()
    line = json.loads(dlpath.read_text().strip())
    assert line["code"] == "TEST_CODE"


# ── 8. Email fail -> Teams fallback ─────────────────────────────────────

@pytest.mark.asyncio
async def test_email_failure_triggers_teams_fallback():
    with _settings_patch():
        teams = AsyncMock(side_effect=[False, True])  # primary fails, fallback succeeds
        with patch("app.services.alert_service._is_suppressed", AsyncMock(return_value=False)), \
             patch("app.services.alert_service._set_cooldown", AsyncMock()), \
             patch("app.services.alert_service._send_email", AsyncMock(return_value=False)), \
             patch("app.services.alert_service._send_teams", teams):
            await send_alert(_incident(severity="warning"))
            assert teams.await_count == 2


# ── 9. send_alert never raises ──────────────────────────────────────────

@pytest.mark.asyncio
async def test_send_alert_never_raises_on_internal_exception():
    with _settings_patch():
        with patch("app.services.alert_service._is_suppressed",
                   AsyncMock(side_effect=RuntimeError("redis down"))), \
             patch("app.services.alert_service._send_email", AsyncMock(return_value=True)):
            # MUST NOT raise — outer try in send_alert catches everything
            await send_alert(_incident(severity="warning"))


# ── 10. AI triage attached for critical when threshold met ──────────────

@pytest.mark.asyncio
async def test_ai_triage_attached_for_critical():
    triage = AiTriage(
        probable_cause="x", confidence=0.9,
        immediate_mitigation="restart", likely_owner="backend",
        first_validation_step="check health",
    )
    captured = []

    async def fake_send_email(inc):
        captured.append(inc)
        return True

    with _settings_patch(teams=""):
        with patch("app.services.alert_service._is_suppressed", AsyncMock(return_value=False)), \
             patch("app.services.alert_service._set_cooldown", AsyncMock()), \
             patch("app.services.alert_service._send_email", side_effect=fake_send_email), \
             patch("app.services.ai_triage.generate_triage", AsyncMock(return_value=triage)):
            await send_alert(_incident(severity="critical"))
    assert captured and captured[0].ai_triage is triage


# ── 11. AI triage skipped for non-critical ──────────────────────────────

@pytest.mark.asyncio
async def test_ai_triage_skipped_for_non_critical():
    with _settings_patch(teams=""):
        triage_mock = AsyncMock(return_value=None)
        with patch("app.services.alert_service._is_suppressed", AsyncMock(return_value=False)), \
             patch("app.services.alert_service._set_cooldown", AsyncMock()), \
             patch("app.services.alert_service._send_email", AsyncMock(return_value=True)), \
             patch("app.services.ai_triage.generate_triage", triage_mock):
            await send_alert(_incident(severity="warning"))
            triage_mock.assert_not_called()


# ── 12. edgar always in recipients ──────────────────────────────────────

@pytest.mark.asyncio
async def test_edgar_always_in_recipients():
    captured = {}

    async def fake_to_thread(fn, *a, **kw):
        # introspect the message by inspecting the closure
        # call the function which builds the message
        return {"status": "Succeeded"}

    # Indirectly: patch EmailClient.from_connection_string to capture message
    sent_messages = []

    class FakeClient:
        @staticmethod
        def from_connection_string(cs):
            return FakeClient()

        def begin_send(self, message):
            sent_messages.append(message)
            class P:
                def result(self_inner):
                    return {"status": "Succeeded"}
            return P()

    fake_module = MagicMock()
    fake_module.EmailClient = FakeClient

    with _settings_patch(teams="", recipients=("someone-else@a.com",)):
        with patch.dict("sys.modules", {"azure.communication.email": fake_module}), \
             patch("app.services.alert_service._is_suppressed", AsyncMock(return_value=False)), \
             patch("app.services.alert_service._set_cooldown", AsyncMock()):
            await send_alert(_incident(severity="warning"))

    assert sent_messages, "ACS email was not invoked"
    to_addresses = [r["address"] for r in sent_messages[0]["recipients"]["to"]]
    assert "edgar.mcochieng@armely.com" in to_addresses


# ── 13. Body builders include incident fields ──────────────────────────

def test_email_body_contains_key_fields():
    inc = _incident()
    body = _build_email_body(inc)
    assert inc.id in body
    assert inc.code in body
    assert "HUMAN VALIDATION REQUIRED" in body


def test_teams_card_structure():
    inc = _incident()
    card = _build_teams_card(inc)
    assert card["type"] == "message"
    assert card["attachments"][0]["contentType"].startswith("application/vnd.microsoft.card.adaptive")


def test_fingerprint_stability():
    inc1 = _incident()
    inc2 = _incident()
    assert _fingerprint(inc1) == _fingerprint(inc2)
    assert _fingerprint(inc1).startswith("alert:cooldown:")
