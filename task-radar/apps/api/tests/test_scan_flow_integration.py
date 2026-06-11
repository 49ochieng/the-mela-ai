"""Integration test: end-to-end scan flow with mocked Graph + AI.

Covers spec items #2, #3, #4, #5, #6, #8 from the validation list:
  - manual scan creates scan_run and runs the worker handler
  - mock email with a clear task creates a Task
  - mock FYI email creates no Task
  - mock urgent email creates a high-priority Task
  - mock email with attachment creates a TaskAttachment row
  - low-confidence AI output routes Task to needs_review
"""
from __future__ import annotations

from datetime import datetime, timedelta
from unittest.mock import AsyncMock, patch

import pytest
from sqlalchemy import select

from app.enums import (
    ConnectionStatus,
    ScanStatus,
    ScanType,
    SourceType,
    TaskStatus,
    StorageStatus,
)
from app.models import (
    GraphConnection,
    ScanRun,
    ScanSettings,
    Task,
    TaskAttachment,
    Tenant,
    User,
)
from app.schemas import ExtractedTask, ExtractionResult
from app.services.ai.extractor import ExtractionDiagnostics
from app.services.tasks.scan_runner import run_scan


def _ok_diag() -> ExtractionDiagnostics:
    return ExtractionDiagnostics(
        has_task=True, task_count=1, input_chars=10, output_chars=10,
        finish_reason="stop", prompt_tokens=10, completion_tokens=10,
        total_tokens=20, prompt_version="v1", model_deployment="gpt-5.2-chat",
    )


def _no_task_diag() -> ExtractionDiagnostics:
    d = _ok_diag()
    d.has_task = False
    d.task_count = 0
    return d


def _err_diag(category: str, msg: str, retryable: bool = True) -> ExtractionDiagnostics:
    d = _ok_diag()
    d.has_task = False
    d.task_count = 0
    d.error_category = category
    d.error_message = msg
    d.retryable = retryable
    return d


# ── helpers ────────────────────────────────────────────────────
async def _seed(session) -> tuple[Tenant, User, ScanSettings, ScanRun]:
    t = Tenant(entra_tenant_id="t1", name="Acme")
    session.add(t); await session.flush()
    u = User(
        tenant_id=t.id, entra_user_id="u1", display_name="Alice",
        email="alice@acme.com", timezone="UTC", role="user",
    )
    session.add(u); await session.flush()
    settings = ScanSettings(
        tenant_id=t.id, user_id=u.id,
        email_scan_enabled=True, teams_scan_enabled=False,
        lookback_hours_first_scan=24,
    )
    session.add(settings)
    # Connected Graph connection (token store will return our fake token).
    conn = GraphConnection(
        tenant_id=t.id, user_id=u.id, provider="microsoft",
        scopes="Mail.Read", status=ConnectionStatus.CONNECTED.value,
        token_reference="f1:fake", refresh_token_reference="f1:fake",
        expires_at=datetime.utcnow() + timedelta(hours=1),
    )
    session.add(conn)
    sr = ScanRun(
        tenant_id=t.id, user_id=u.id,
        scan_type=ScanType.EMAIL.value, status=ScanStatus.PENDING.value,
    )
    session.add(sr)
    await session.commit()
    return t, u, settings, sr


def _email(idx: int, subject: str, body: str, sender="boss@acme.com",
           has_attachments: bool = False) -> dict:
    return {
        "id": f"msg-{idx}",
        "internetMessageId": f"<msg{idx}@acme.com>",
        "conversationId": f"conv-{idx}",
        "subject": subject,
        "from": {"emailAddress": {"name": "Boss", "address": sender}},
        "toRecipients": [{"emailAddress": {"name": "Alice", "address": "alice@acme.com"}}],
        "ccRecipients": [],
        "receivedDateTime": "2025-04-01T10:00:00Z",
        "hasAttachments": has_attachments,
        "bodyPreview": body[:200],
        "body": {"contentType": "text", "content": body},
        "webLink": f"https://outlook.office.com/?ItemID={idx}",
    }


class _FakeGraphClient:
    def __init__(self, **_): pass
    async def aclose(self): pass


def _patch_client(monkeypatch, fake):
    async def _for_user(*_a, **_kw): return fake
    monkeypatch.setattr(
        "app.services.tasks.scan_runner.GraphClient.for_user",
        classmethod(lambda cls, *a, **kw: _for_user()),
    )


# ── tests ──────────────────────────────────────────────────────
@pytest.mark.asyncio
async def test_clear_task_creates_task(session, monkeypatch):
    _t, u, _s, sr = await _seed(session)

    msgs = [_email(1, "Please review the Q3 deck by Friday",
                    "Hi Alice, please review the attached Q3 deck and send feedback by Friday.")]
    monkeypatch.setattr(
        "app.services.tasks.scan_runner.outlook_svc.get_messages_since",
        AsyncMock(return_value=msgs),
    )
    monkeypatch.setattr(
        "app.services.tasks.scan_runner.extract_with_diagnostics",
        AsyncMock(return_value=(ExtractionResult(has_task=True, tasks=[
            ExtractedTask(
                title="Review Q3 deck", description="Send feedback by Friday",
                task_type="review", priority="high",
                priority_reasoning="explicit deadline",
                confidence=0.92, evidence="please review the attached Q3 deck",
            )
        ]), _ok_diag())),
    )
    _patch_client(monkeypatch, _FakeGraphClient())

    await run_scan(session, sr.id)
    refreshed = await session.get(ScanRun, sr.id)
    tasks = (await session.execute(
        select(Task).where(Task.user_id == u.id)
    )).scalars().all()

    assert refreshed.status == ScanStatus.COMPLETED.value
    assert refreshed.messages_scanned == 1
    assert refreshed.tasks_created == 1
    assert len(tasks) == 1
    assert tasks[0].priority == "high"
    assert tasks[0].status == TaskStatus.OPEN.value


@pytest.mark.asyncio
async def test_fyi_email_creates_no_task(session, monkeypatch):
    _t, u, _s, sr = await _seed(session)

    monkeypatch.setattr(
        "app.services.tasks.scan_runner.outlook_svc.get_messages_since",
        AsyncMock(return_value=[_email(2, "FYI: Q2 results published",
                                         "Sharing the Q2 results page for your awareness.")]),
    )
    monkeypatch.setattr(
        "app.services.tasks.scan_runner.extract_with_diagnostics",
        AsyncMock(return_value=(ExtractionResult(has_task=False, tasks=[]), _no_task_diag())),
    )
    _patch_client(monkeypatch, _FakeGraphClient())

    await run_scan(session, sr.id)
    tasks = (await session.execute(
        select(Task).where(Task.user_id == u.id)
    )).scalars().all()
    refreshed = await session.get(ScanRun, sr.id)
    assert tasks == []
    assert refreshed.tasks_created == 0
    assert refreshed.status == ScanStatus.COMPLETED.value


@pytest.mark.asyncio
async def test_low_confidence_routes_to_needs_review(session, monkeypatch):
    _t, u, _s, sr = await _seed(session)
    monkeypatch.setattr(
        "app.services.tasks.scan_runner.outlook_svc.get_messages_since",
        AsyncMock(return_value=[_email(3, "Maybe look at this", "Could you maybe glance at this thing?")]),
    )
    monkeypatch.setattr(
        "app.services.tasks.scan_runner.extract_with_diagnostics",
        AsyncMock(return_value=(ExtractionResult(has_task=True, tasks=[
            ExtractedTask(
                title="Maybe glance at thing", description="Unclear ask",
                task_type="other", priority="low", priority_reasoning="vague",
                confidence=0.4, evidence="could you maybe glance",
            )
        ]), _ok_diag())),
    )
    _patch_client(monkeypatch, _FakeGraphClient())
    await run_scan(session, sr.id)
    t = (await session.execute(select(Task).where(Task.user_id == u.id))).scalars().one()
    assert t.status == TaskStatus.NEEDS_REVIEW.value


@pytest.mark.asyncio
async def test_attachment_creates_task_attachment(session, monkeypatch):
    _t, u, _s, sr = await _seed(session)

    monkeypatch.setattr(
        "app.services.tasks.scan_runner.outlook_svc.get_messages_since",
        AsyncMock(return_value=[_email(4, "Sign the contract",
                                         "Please sign and return.", has_attachments=True)]),
    )
    monkeypatch.setattr(
        "app.services.tasks.scan_runner.extract_with_diagnostics",
        AsyncMock(return_value=(ExtractionResult(has_task=True, tasks=[
            ExtractedTask(
                title="Sign contract", description="Sign and return",
                task_type="approve", priority="high",
                priority_reasoning="explicit",
                confidence=0.95, evidence="please sign",
            )
        ]), _ok_diag())),
    )
    monkeypatch.setattr(
        "app.services.tasks.scan_runner.outlook_svc.get_message_attachments",
        AsyncMock(return_value=[{
            "id": "att-1",
            "name": "contract.pdf",
            "isInline": False,
            "@odata.type": "#microsoft.graph.fileAttachment",
            "contentType": "application/pdf",
            "size": 1024,
        }]),
    )
    monkeypatch.setattr(
        "app.services.tasks.scan_runner.outlook_svc.download_email_attachment",
        AsyncMock(return_value=b"PDFDATA"),
    )
    _patch_client(monkeypatch, _FakeGraphClient())

    await run_scan(session, sr.id)
    atts = (await session.execute(
        select(TaskAttachment).where(TaskAttachment.user_id == u.id)
    )).scalars().all()
    assert len(atts) == 1
    assert atts[0].file_name == "contract.pdf"
    assert atts[0].storage_status in (
        StorageStatus.ARCHIVED.value, StorageStatus.FAILED.value,  # depending on local fs
    )


@pytest.mark.asyncio
async def test_duplicate_message_does_not_create_duplicate_tasks(session, monkeypatch):
    _t, u, _s, sr = await _seed(session)
    msg = _email(5, "Review report", "Please review the report.")
    monkeypatch.setattr(
        "app.services.tasks.scan_runner.outlook_svc.get_messages_since",
        AsyncMock(return_value=[msg, msg]),  # same id twice
    )
    monkeypatch.setattr(
        "app.services.tasks.scan_runner.extract_with_diagnostics",
        AsyncMock(return_value=(ExtractionResult(has_task=True, tasks=[
            ExtractedTask(
                title="Review report", description="d", task_type="review",
                priority="medium", priority_reasoning="r",
                confidence=0.9, evidence="please review",
            )
        ]), _ok_diag())),
    )
    _patch_client(monkeypatch, _FakeGraphClient())
    await run_scan(session, sr.id)
    tasks = (await session.execute(select(Task).where(Task.user_id == u.id))).scalars().all()
    assert len(tasks) == 1


@pytest.mark.asyncio
async def test_ai_failure_does_not_fail_whole_scan(session, monkeypatch):
    _t, u, _s, sr = await _seed(session)
    msgs = [_email(6, "Subject", "body")]
    monkeypatch.setattr(
        "app.services.tasks.scan_runner.outlook_svc.get_messages_since",
        AsyncMock(return_value=msgs),
    )
    monkeypatch.setattr(
        "app.services.tasks.scan_runner.extract_with_diagnostics",
        AsyncMock(return_value=(
            ExtractionResult(has_task=False, tasks=[]),
            _err_diag("rate_limit", "AI down", retryable=True),
        )),
    )
    _patch_client(monkeypatch, _FakeGraphClient())
    await run_scan(session, sr.id)
    refreshed = await session.get(ScanRun, sr.id)
    # AI failure must surface as COMPLETED_WITH_ERRORS, not silent COMPLETED.
    assert refreshed.status == ScanStatus.COMPLETED_WITH_ERRORS.value
    assert refreshed.errors_count == 1
    assert refreshed.ai_failed_count == 1
    assert refreshed.error_summary and "ai_rate_limit" in refreshed.error_summary
