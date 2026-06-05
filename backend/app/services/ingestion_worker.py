"""
Mela AI - Enterprise Ingestion Worker
In-process queue for connector sync jobs.
Supports full_sync, delta_sync, reindex, health_check job types.
Delta tokens are stored in memory only — never logged.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Dict, List, Optional

from app.core.config import settings

logger = logging.getLogger(__name__)


def _fire_ops_alert(
    *,
    code: str,
    title: str,
    message: str,
    severity: str = "critical",
    route: Optional[str] = None,
    worker: Optional[str] = None,
    tenant_id: Optional[str] = None,
    stack_trace: str = "",
) -> None:
    """Best-effort ops alert for worker blind-spot failures."""
    try:
        import asyncio as _asyncio
        from app.services.alert_service import send_alert, AlertIncident
        incident = AlertIncident(
            title=title[:200],
            severity=severity,
            code=code,
            route=route,
            worker=worker,
            tenant_id=tenant_id,
            error_message=message[:500],
            stack_trace=(stack_trace or "")[:3000],
        )
        try:
            loop = _asyncio.get_running_loop()
            loop.create_task(send_alert(incident))
        except RuntimeError:
            _asyncio.run(send_alert(incident))
    except Exception:
        # Never let alert delivery break the worker.
        pass


class JobType(str, Enum):
    FULL_SYNC = "full_sync"
    DELTA_SYNC = "delta_sync"
    REINDEX = "reindex"
    HEALTH_CHECK = "health_check"
    ACL_REFRESH = "acl_refresh"


class JobStatus(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    DONE = "done"
    FAILED = "failed"
    DEAD_LETTER = "dead_letter"


@dataclass
class SyncJob:
    id: str
    job_type: JobType
    connector_type: str
    source_id: str
    workspace_id: str
    context_type: str = "org"
    status: JobStatus = JobStatus.PENDING
    attempts: int = 0
    max_attempts: int = 5
    created_at: datetime = field(default_factory=datetime.utcnow)
    started_at: Optional[datetime] = None
    finished_at: Optional[datetime] = None
    docs_processed: int = 0
    error: Optional[str] = None
    # Delegated access token for user-scoped connectors (OneDrive).
    # Never logged or persisted.
    delegated_token: Optional[str] = None
    user_id: Optional[str] = None


class IngestionWorker:
    """
    Lightweight in-process job queue.
    For production, replace with Azure Service Bus or Redis Streams.
    """

    MAX_JOB_HISTORY = 200

    def __init__(self) -> None:
        self._queue: asyncio.Queue = asyncio.Queue()
        self._jobs: Dict[str, SyncJob] = {}
        self._running = False
        # Delta tokens per source_id — stored in memory, never logged
        self._delta_tokens: Dict[str, str] = {}
        # Lightweight stats per connector type
        self._stats: Dict[str, Dict] = {}

    # ── Public API ────────────────────────────────────────────────────────────

    def enqueue(self, job: SyncJob) -> str:
        self._jobs[job.id] = job
        self._queue.put_nowait(job)
        logger.info(
            "Job enqueued: id=%s type=%s connector=%s source=%s",
            job.id, job.job_type, job.connector_type, job.source_id,
        )
        self._trim_history()
        return job.id

    def get_job(self, job_id: str) -> Optional[SyncJob]:
        return self._jobs.get(job_id)

    def list_jobs(self, connector_type: Optional[str] = None) -> List[SyncJob]:
        jobs = list(self._jobs.values())
        if connector_type:
            jobs = [j for j in jobs if j.connector_type == connector_type]
        return sorted(jobs, key=lambda j: j.created_at, reverse=True)

    def get_stats(self, key: str) -> Dict:
        return self._stats.get(key, {
            "last_sync": None, "docs_indexed": 0, "errors": 0,
        })

    def set_delta_token(self, source_id: str, token: str) -> None:
        # Never log the token value
        self._delta_tokens[source_id] = token
        # Persist to DB (best-effort, async)
        asyncio.ensure_future(self._persist_delta_token(source_id, token))

    def get_delta_token(self, source_id: str) -> Optional[str]:
        return self._delta_tokens.get(source_id)

    async def load_delta_tokens_from_db(self) -> None:
        """Load persisted delta tokens from DB into the in-memory cache.

        Covers both SharePoint and OneDrive tokens (keyed as 'onedrive:{user_id}').
        """
        try:
            from app.core.database import async_session_maker, db_available
            from app.models.models import ConnectorState
            from sqlalchemy import select
            if not db_available:
                return
            async with async_session_maker() as db:
                stmt = select(ConnectorState).where(
                    ConnectorState.state_key == "delta_token"
                )
                result = await db.execute(stmt)
                for row in result.scalars().all():
                    self._delta_tokens[row.source_id] = row.state_value
            if self._delta_tokens:
                od_count = sum(1 for k in self._delta_tokens if k.startswith("onedrive:"))
                sp_count = len(self._delta_tokens) - od_count
                logger.info(
                    "Loaded %d delta tokens from DB (sharepoint=%d, onedrive=%d)",
                    len(self._delta_tokens), sp_count, od_count,
                )
        except Exception as e:
            logger.warning("Failed to load delta tokens from DB: %s", e)

    async def _persist_delta_token(self, source_id: str, token: str) -> None:
        """Persist a delta token to the DB."""
        try:
            from app.core.database import async_session_maker, db_available
            from app.models.models import ConnectorState
            from sqlalchemy import select
            if not db_available:
                return
            # Infer connector_type from source_id prefix (e.g. "onedrive:...")
            _ct = "onedrive" if source_id.startswith("onedrive:") else "sharepoint"
            async with async_session_maker() as db:
                stmt = select(ConnectorState).where(
                    ConnectorState.source_id == source_id,
                    ConnectorState.state_key == "delta_token",
                )
                result = await db.execute(stmt)
                existing = result.scalar_one_or_none()
                if existing:
                    existing.state_value = token
                else:
                    from datetime import datetime as _dt
                    import uuid as _uuid
                    db.add(ConnectorState(
                        id=str(_uuid.uuid4()),
                        connector_type=_ct,
                        source_id=source_id,
                        state_key="delta_token",
                        state_value=token,
                    ))
                await db.commit()
        except Exception as e:
            logger.warning("Failed to persist delta token for %s: %s", source_id, e)

    async def auto_queue_onedrive_for_known_users(self) -> None:
        """On startup, enqueue a delta sync for every user who has a persisted OneDrive delta token.

        This ensures background sync resumes after a restart without needing a
        new delegated token from the user.
        """
        from app.core.config import settings as _settings
        if not _settings.CONNECTOR_ONEDRIVE_ENABLED:
            return
        try:
            from app.core.database import async_session_maker, db_available
            from app.models.models import ConnectorState
            from sqlalchemy import select
            if not db_available:
                return
            async with async_session_maker() as db:
                stmt = select(ConnectorState).where(
                    ConnectorState.connector_type == "onedrive",
                    ConnectorState.state_key == "delta_token",
                )
                result = await db.execute(stmt)
                rows = result.scalars().all()

            if not rows:
                logger.info("No OneDrive users found in DB — skipping auto-queue")
                return

            workspace_id = _settings.effective_tenant_id or "default"
            for row in rows:
                source_id = row.source_id  # e.g. "onedrive:<user_oid>"
                user_id = source_id.removeprefix("onedrive:")
                if not user_id:
                    continue
                job = SyncJob(
                    id=str(__import__("uuid").uuid4()),
                    job_type=JobType.DELTA_SYNC,
                    connector_type="onedrive",
                    source_id=source_id,
                    workspace_id=workspace_id,
                    context_type="personal",
                    user_id=user_id,
                )
                self.enqueue(job)
                logger.info("Auto-queued OneDrive delta sync for user %s", user_id)
        except Exception as e:
            logger.warning("auto_queue_onedrive_for_known_users failed (non-fatal): %s", e)

    # ── Job execution ─────────────────────────────────────────────────────────

    async def run_job(self, job: SyncJob) -> None:
        """Execute one job. On failure retries with exponential back-off."""
        job.status = JobStatus.RUNNING
        job.started_at = datetime.utcnow()
        job.attempts += 1

        try:
            count = await self._execute(job)
            job.status = JobStatus.DONE
            job.finished_at = datetime.utcnow()
            job.docs_processed = count
            self._stats[job.connector_type] = {
                "last_sync": datetime.utcnow().isoformat(),
                "docs_indexed": count,
                "errors": 0,
            }
            logger.info("Job %s done — %d docs", job.id, count)

        except Exception as exc:
            msg = str(exc)
            logger.error(
                "Job %s failed (attempt %d/%d): %s",
                job.id, job.attempts, job.max_attempts, msg,
            )
            prev = self._stats.get(job.connector_type, {})
            self._stats[job.connector_type] = {
                **prev,
                "errors": prev.get("errors", 0) + 1,
            }
            job.error = msg

            if job.attempts < job.max_attempts:
                delay = min(2 ** job.attempts, 300)
                job.status = JobStatus.PENDING
                await asyncio.sleep(delay)
                self._queue.put_nowait(job)
            else:
                job.status = JobStatus.DEAD_LETTER
                logger.error("Job %s → dead letter after %d attempts", job.id, job.attempts)
                _fire_ops_alert(
                    code="DLQ_EXHAUSTED",
                    title=f"Ingestion job dead-letter: {job.connector_type}/{job.source_id}",
                    message=(
                        f"Job {job.id} moved to dead-letter after "
                        f"{job.attempts} attempts "
                        f"(connector={job.connector_type}, "
                        f"source={job.source_id}, type={job.job_type}). "
                        f"Last error: {msg}"
                    ),
                    severity="critical",
                    route="worker:ingestion_dead_letter",
                    worker=job.connector_type,
                    tenant_id=job.workspace_id,
                )

    async def _execute(self, job: SyncJob) -> int:
        """Route to the correct connector and ingest all yielded documents."""
        from app.services.search.ingestion import ingestion_pipeline

        # ACL refresh: re-fetch permissions for all docs under a site/drive
        if job.job_type == JobType.ACL_REFRESH:
            return await self._execute_acl_refresh(job)

        connector = await self._get_connector(job)
        if connector is None:
            return 0

        full = job.job_type in (JobType.FULL_SYNC, JobType.REINDEX)
        # For OneDrive, the canonical delta-token key is "onedrive:<user_id>"
        if job.connector_type == "onedrive" and job.user_id:
            delta_key = f"onedrive:{job.user_id}"
            # Ensure source_id is also aligned so token persistence is consistent
            job.source_id = delta_key
        else:
            delta_key = job.source_id
        delta_token = None if full else self.get_delta_token(delta_key)

        count = 0
        if hasattr(connector, "sync"):
            import inspect
            sig = inspect.signature(connector.sync)
            if "delta_token" in sig.parameters:
                # New-style connectors (OneDrive v2) accept delta_token directly
                async for doc in connector.sync(full=full, delta_token=delta_token):
                    await ingestion_pipeline.ingest_document(doc)
                    count += 1
            else:
                async for doc in connector.sync(full=full):
                    await ingestion_pipeline.ingest_document(doc)
                    count += 1

        # Persist new delta token from the connector if supported
        if hasattr(connector, "get_delta_token"):
            new_token = connector.get_delta_token(delta_key)
            if new_token:
                self.set_delta_token(delta_key, new_token)

        return count

    async def _get_connector(self, job: SyncJob):
        ct = job.connector_type
        wid = job.workspace_id
        ctx = job.context_type

        if ct == "sharepoint" and settings.CONNECTOR_SHAREPOINT_ENABLED:
            from app.services.connectors.sharepoint import SharePointConnector
            return SharePointConnector(wid, ctx)

        if ct == "onedrive" and settings.CONNECTOR_ONEDRIVE_ENABLED:
            from app.services.connectors.onedrive import OneDriveConnector
            if not job.user_id:
                logger.warning("OneDrive job %s missing user_id — skipping", job.id)
                return None
            return OneDriveConnector(
                wid, ctx,
                user_id=job.user_id,
            )

        if ct == "email" and settings.CONNECTOR_EMAIL_ENABLED:
            from app.services.connectors.email_connector import EmailConnector
            return EmailConnector(wid, ctx)

        if ct == "planner" and settings.CONNECTOR_PLANNER_ENABLED:
            from app.services.connectors.planner import PlannerConnector
            return PlannerConnector(wid, ctx)

        if ct == "org_website" and settings.CONNECTOR_ORG_WEBSITE_ENABLED:
            from app.services.connectors.org_website import OrgWebsiteConnector
            return OrgWebsiteConnector(wid, ctx)

        logger.warning("Connector '%s' not enabled or unknown", ct)
        return None

    async def _execute_acl_refresh(self, job: SyncJob) -> int:
        """Re-fetch and upsert ACL fields for all documents in the given source.

        For SharePoint sources, iterates through the drive and upserts only
        the acl_users, acl_groups, and acl_last_refreshed fields for each
        document — content is NOT re-extracted or re-embedded.
        """
        try:
            from app.services.search.ingestion import ingestion_pipeline
            from app.services.connectors.graph_client import GraphClient
            from datetime import datetime as _dt, timezone as _tz
            import uuid as _uuid

            gc = GraphClient()
            # Determine site+drive from source_id (format: "{site_id}::{drive_id}")
            source_id = job.source_id
            if "::" not in source_id:
                logger.warning("ACL refresh: invalid source_id format '%s'", source_id)
                return 0

            site_id, drive_id = source_id.split("::", 1)
            now_iso = _dt.now(_tz.utc).isoformat()

            # Page through the drive delta to get current items & their permissions
            items, _ = await gc.get_drive_delta(site_id, drive_id, delta_token=None)
            updated = 0
            for item in items:
                item_id = item.get("id")
                if not item_id:
                    continue
                # Fetch permissions for this item
                perm_resp = await gc._get(
                    f"/drives/{drive_id}/items/{item_id}/permissions"
                )
                perms = perm_resp.get("value", [])
                acl_users: list = []
                acl_groups: list = []
                for p in perms:
                    granted_to = p.get("grantedToV2") or p.get("grantedTo") or {}
                    uid = (granted_to.get("user") or {}).get("id")
                    gid = (granted_to.get("group") or {}).get("id")
                    if uid:
                        acl_users.append(uid)
                    if gid:
                        acl_groups.append(gid)

                # Upsert only ACL fields (merge=True to preserve content/vector)
                doc_id_prefix = f"sp:{site_id}:{drive_id}:{item_id}"
                patch = {
                    "acl_users": acl_users,
                    "acl_groups": acl_groups,
                    "acl_last_refreshed": now_iso,
                }
                try:
                    await ingestion_pipeline.index_manager.upsert_documents(
                        ingestion_pipeline.index_name,
                        [patch | {"id": doc_id_prefix}],
                        merge=True,
                    )
                    updated += 1
                except Exception as _ue:
                    logger.debug("ACL upsert failed for %s: %s", doc_id_prefix, _ue)

            logger.info(
                "ACL refresh for source %s complete — %d items updated",
                source_id, updated,
            )
            return updated
        except Exception as exc:
            logger.error("ACL refresh job %s failed: %s", job.id, exc)
            _fire_ops_alert(
                code="ACL_REFRESH_FAILED",
                title=f"ACL refresh failed: {job.source_id}",
                message=(
                    f"ACL refresh failed for job={job.id} "
                    f"source={job.source_id}: {exc}"
                ),
                severity="warning",
                route="worker:acl_refresh",
                worker=job.connector_type,
                tenant_id=job.workspace_id,
            )
            raise

    # ── Background loop ───────────────────────────────────────────────────────

    async def process_queue(self) -> None:
        """Drain the queue continuously. Call this in a background asyncio task."""
        self._running = True
        self._consecutive_loop_failures = 0
        logger.info("Ingestion worker started")
        while self._running:
            try:
                job = await asyncio.wait_for(self._queue.get(), timeout=5.0)
                await self.run_job(job)
                self._queue.task_done()
                self._consecutive_loop_failures = 0
            except asyncio.TimeoutError:
                continue
            except Exception as exc:
                self._consecutive_loop_failures += 1
                logger.error(
                    "Worker loop error (%d consecutive): %s",
                    self._consecutive_loop_failures, exc,
                )
                if self._consecutive_loop_failures >= 3:
                    _fire_ops_alert(
                        code="WORKER_LOOP_FAILURE",
                        title=f"Ingestion worker loop failing ({self._consecutive_loop_failures} consecutive)",
                        message=str(exc),
                        severity="critical",
                        route="worker:process_queue",
                        worker="ingestion",
                    )
                    self._consecutive_loop_failures = 0

    def stop(self) -> None:
        self._running = False

    def _trim_history(self) -> None:
        if len(self._jobs) > self.MAX_JOB_HISTORY:
            oldest = sorted(self._jobs.values(), key=lambda j: j.created_at)
            for j in oldest[: len(self._jobs) - self.MAX_JOB_HISTORY]:
                del self._jobs[j.id]


# Singleton
ingestion_worker = IngestionWorker()
