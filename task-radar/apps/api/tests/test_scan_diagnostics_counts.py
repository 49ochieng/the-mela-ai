"""Scan diagnostics: a mixed run should populate every per-stage counter
(noise / dup / no-task / success / failure) and emit ScanEvent rows."""
from __future__ import annotations

from datetime import datetime, timedelta
from unittest.mock import AsyncMock

import pytest
from sqlalchemy import select

from app.enums import ConnectionStatus, ScanStatus, ScanType
from app.models import (
    GraphConnection, ScanEvent, ScanRun, ScanSettings, Tenant, User,
)
from app.schemas import ExtractedTask, ExtractionResult
from app.services.ai.extractor import ExtractionDiagnostics
from app.services.tasks.scan_runner import run_scan


def _email(idx: int, subject: str, body: str) -> dict:
    return {
        "id": f"m{idx}", "internetMessageId": f"<m{idx}@x>",
        "conversationId": f"c{idx}", "subject": subject,
        "from": {"emailAddress": {"name": "Boss", "address": "boss@x.com"}},
        "toRecipients": [{"emailAddress": {"name": "A", "address": "a@x.com"}}],
        "ccRecipients": [], "receivedDateTime": "2025-04-01T10:00:00Z",
        "hasAttachments": False, "bodyPreview": body[:50],
        "body": {"contentType": "text", "content": body},
        "webLink": f"https://x/{idx}",
    }


async def _seed(session) -> tuple[User, ScanRun]:
    t = Tenant(entra_tenant_id="t", name="A"); session.add(t); await session.flush()
    u = User(tenant_id=t.id, entra_user_id="u", display_name="A",
             email="a@x.com", timezone="UTC", role="user")
    session.add(u); await session.flush()
    session.add(ScanSettings(tenant_id=t.id, user_id=u.id,
                              email_scan_enabled=True))
    session.add(GraphConnection(
        tenant_id=t.id, user_id=u.id, provider="microsoft",
        scopes="Mail.Read", status=ConnectionStatus.CONNECTED.value,
        token_reference="f1:fake", refresh_token_reference="f1:fake",
        expires_at=datetime.utcnow() + timedelta(hours=1),
    ))
    sr = ScanRun(tenant_id=t.id, user_id=u.id,
                 scan_type=ScanType.EMAIL.value,
                 status=ScanStatus.PENDING.value)
    session.add(sr); await session.commit()
    return u, sr


def _ok(task=True):
    d = ExtractionDiagnostics(
        has_task=task, task_count=1 if task else 0,
        input_chars=10, output_chars=10, finish_reason="stop",
        prompt_tokens=10, completion_tokens=10, total_tokens=20,
        prompt_version="v1", model_deployment="gpt-5.2-chat",
    )
    return d


def _err(cat: str):
    d = _ok(task=False)
    d.error_category = cat
    d.error_message = f"{cat} happened"
    d.retryable = True
    return d


@pytest.mark.asyncio
async def test_scan_diagnostics_counts_and_events(session, monkeypatch):
    u, sr = await _seed(session)

    msgs = [
        _email(1, "Please review the deck by Friday", "Please review the deck."),  # success
        _email(2, "Out of office", "I am OOO until Monday — auto-reply"),          # noise
        _email(3, "FYI: nothing actionable", "Just sharing for awareness."),        # no_task
        _email(4, "AI broken", "trigger ai failure"),                               # ai_failed
    ]
    monkeypatch.setattr(
        "app.services.tasks.scan_runner.outlook_svc.get_messages_since",
        AsyncMock(return_value=msgs),
    )

    seq = [
        (ExtractionResult(has_task=True, tasks=[ExtractedTask(
            title="Review deck", description="d", task_type="review",
            priority="high", priority_reasoning="explicit",
            confidence=0.9, evidence="please review",
        )]), _ok(True)),
        # msg 2 won't reach AI (noise filter)
        (ExtractionResult(has_task=False, tasks=[]), _ok(False)),
        (ExtractionResult(has_task=False, tasks=[]), _err("rate_limit")),
    ]

    async def fake_extract(*_a, **_kw):
        return seq.pop(0)

    monkeypatch.setattr(
        "app.services.tasks.scan_runner.extract_with_diagnostics", fake_extract,
    )

    class _FakeClient:
        async def aclose(self): pass
    async def _for_user(*_a, **_kw): return _FakeClient()
    monkeypatch.setattr(
        "app.services.tasks.scan_runner.GraphClient.for_user",
        classmethod(lambda cls, *a, **kw: _for_user()),
    )

    await run_scan(session, sr.id)
    refreshed = await session.get(ScanRun, sr.id)

    assert refreshed.messages_scanned == 4
    assert refreshed.noise_skipped_count >= 1, "OOO should be noise-filtered"
    assert refreshed.ai_attempted_count == 3
    assert refreshed.ai_success_count == 2  # one success + one no_task
    assert refreshed.ai_no_task_count == 1
    assert refreshed.ai_failed_count == 1
    assert refreshed.tasks_created == 1
    assert refreshed.errors_count == 1
    assert refreshed.status == ScanStatus.COMPLETED_WITH_ERRORS.value
    assert refreshed.error_categories_json.get("ai_rate_limit") == 1

    events = (await session.execute(
        select(ScanEvent).where(ScanEvent.scan_run_id == sr.id)
    )).scalars().all()
    statuses = {e.status for e in events}
    assert "success" in statuses
    assert "no_task" in statuses
    assert "error" in statuses
