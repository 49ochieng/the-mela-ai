"""Ingestion worker alerting regression tests.

These tests protect proactive alert hooks for background worker failures.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from app.services.ingestion_worker import (
    IngestionWorker,
    JobStatus,
    JobType,
    SyncJob,
)


@pytest.mark.asyncio
async def test_dead_letter_transition_fires_ops_alert() -> None:
    worker = IngestionWorker()
    job = SyncJob(
        id="job-dead-letter-1",
        job_type=JobType.DELTA_SYNC,
        connector_type="sharepoint",
        source_id="site::drive",
        workspace_id="tenant-a",
        context_type="org",
        max_attempts=1,
    )

    with patch.object(worker, "_execute", AsyncMock(side_effect=RuntimeError("connector timeout"))), \
        patch("app.services.ingestion_worker._fire_ops_alert") as alert_spy:
        await worker.run_job(job)

    assert job.status == JobStatus.DEAD_LETTER
    assert job.attempts == 1
    assert alert_spy.call_count == 1

    kwargs = alert_spy.call_args.kwargs
    assert kwargs["code"] == "DLQ_EXHAUSTED"
    assert kwargs["severity"] == "critical"
    assert kwargs["route"] == "worker:ingestion_dead_letter"
    assert "job-dead-letter-1" in kwargs["message"]
