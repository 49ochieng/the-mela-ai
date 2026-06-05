"""
Mela AI - Enterprise Connectors API Endpoints
Provides connector status, health checks, sync triggers, and index statistics.
"""

from __future__ import annotations

import logging
import uuid
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from app.core.config import settings
from app.core.security import get_current_user
from app.schemas.auth import UserInfo

logger = logging.getLogger(__name__)
router = APIRouter()


# ── Schemas ───────────────────────────────────────────────────────────────────

class ConnectorStatusResponse(BaseModel):
    connector_type: str
    enabled: bool
    healthy: Optional[bool] = None
    health_message: str = ""
    latency_ms: float = 0.0
    last_sync: Optional[str] = None
    docs_indexed: int = 0
    errors: int = 0


class SyncRequest(BaseModel):
    source_id: str
    full_sync: bool = False


class OneDriveSyncRequest(BaseModel):
    full_sync: bool = False
    # delegated_token kept for backward compat but no longer required
    delegated_token: str = ""


class CrawlRuleRequest(BaseModel):
    include_libraries: Optional[List[str]] = None
    exclude_paths: Optional[List[str]] = None


class JobResponse(BaseModel):
    job_id: str
    status: str
    connector_type: str
    source_id: str


# ── Helpers ───────────────────────────────────────────────────────────────────

_CONNECTOR_FLAGS = {
    "sharepoint":   lambda: settings.CONNECTOR_SHAREPOINT_ENABLED,
    "onedrive":     lambda: settings.CONNECTOR_ONEDRIVE_ENABLED,
    "email":        lambda: settings.CONNECTOR_EMAIL_ENABLED,
    "planner":      lambda: settings.CONNECTOR_PLANNER_ENABLED,
    "org_website":  lambda: settings.CONNECTOR_ORG_WEBSITE_ENABLED,
    "public_web":   lambda: settings.CONNECTOR_PUBLIC_WEB_ENABLED,
}


async def _instantiate(connector_type: str, workspace_id: str, context_type: str = "org"):
    if connector_type == "sharepoint":
        from app.services.connectors.sharepoint import SharePointConnector
        return SharePointConnector(workspace_id, context_type)
    if connector_type == "onedrive":
        from app.services.connectors.onedrive import OneDriveConnector
        return OneDriveConnector(workspace_id, context_type)
    if connector_type == "email":
        from app.services.connectors.email_connector import EmailConnector
        return EmailConnector(workspace_id, context_type)
    if connector_type == "planner":
        from app.services.connectors.planner import PlannerConnector
        return PlannerConnector(workspace_id, context_type)
    if connector_type == "org_website":
        from app.services.connectors.org_website import OrgWebsiteConnector
        return OrgWebsiteConnector(workspace_id, context_type)
    if connector_type == "public_web":
        from app.services.connectors.public_web import PublicWebConnector
        return PublicWebConnector(workspace_id, context_type)
    raise HTTPException(status_code=400, detail=f"Unknown connector: {connector_type}")


# ── Endpoints ─────────────────────────────────────────────────────────────────

@router.get("/status", response_model=List[ConnectorStatusResponse])
async def list_connector_statuses(
    current_user: UserInfo = Depends(get_current_user),
):
    """Return enabled/health/sync status for all connectors."""
    from app.services.ingestion_worker import ingestion_worker

    results = []
    for ct, flag_fn in _CONNECTOR_FLAGS.items():
        enabled = flag_fn()
        stats = ingestion_worker.get_stats(ct)
        results.append(ConnectorStatusResponse(
            connector_type=ct,
            enabled=enabled,
            last_sync=stats.get("last_sync"),
            docs_indexed=stats.get("docs_indexed", 0),
            errors=stats.get("errors", 0),
        ))
    return results


@router.post("/{connector_type}/test")
async def test_connector(
    connector_type: str,
    current_user: UserInfo = Depends(get_current_user),
):
    """Run a health check for a connector."""
    workspace_id = settings.effective_tenant_id or getattr(current_user, "azure_id", str(current_user.id))
    try:
        conn = await _instantiate(connector_type, workspace_id)
        ok = await conn.health_check()
        return {
            "status": "ok" if ok else "error",
            "message": f"{connector_type} {'reachable' if ok else 'unreachable'}",
        }
    except HTTPException:
        raise
    except Exception as exc:
        logger.error("Health check error for %s: %s", connector_type, str(exc))
        return {"status": "error", "message": "Health check failed"}


@router.get("/{connector_type}/sources")
async def list_sources(
    connector_type: str,
    current_user: UserInfo = Depends(get_current_user),
):
    """List available sources (sites, drives, folders) for a connector."""
    workspace_id = settings.effective_tenant_id or getattr(current_user, "azure_id", str(current_user.id))
    try:
        conn = await _instantiate(connector_type, workspace_id)
        # Only connectors with list_sources method
        if not hasattr(conn, "list_sources"):
            return {"sources": [], "count": 0}
        # Gather sources by a short crawl of metadata
        sources = []
        if connector_type == "sharepoint":
            sources = await conn.list_sources() if hasattr(conn, "list_sources") else []
        return {"sources": sources, "count": len(sources)}
    except HTTPException:
        raise
    except Exception as exc:
        logger.error("list_sources error for %s: %s", connector_type, str(exc))
        raise HTTPException(status_code=500, detail="Failed to list sources")


@router.post("/{connector_type}/sync", response_model=JobResponse)
async def trigger_sync(
    connector_type: str,
    req: SyncRequest,
    current_user: UserInfo = Depends(get_current_user),
):
    """Enqueue a sync job for a connector source."""
    from app.services.ingestion_worker import ingestion_worker, SyncJob, JobType

    # Use shared tenant workspace so all users can search org content.
    workspace_id = settings.effective_tenant_id or getattr(current_user, "azure_id", str(current_user.id))
    job = SyncJob(
        id=str(uuid.uuid4()),
        job_type=JobType.FULL_SYNC if req.full_sync else JobType.DELTA_SYNC,
        connector_type=connector_type,
        source_id=req.source_id,
        workspace_id=workspace_id,
        context_type="org",
    )
    job_id = ingestion_worker.enqueue(job)

    # NOTE: Do NOT also call run_job() via background_tasks — the ingestion
    # worker's process_queue() loop already drains the queue and executes
    # each job exactly once.  Adding background_tasks.add_task() here would
    # cause the job to run twice.

    return JobResponse(
        job_id=job_id, status="queued",
        connector_type=connector_type, source_id=req.source_id,
    )


@router.post("/sharepoint/reindex")
async def reindex_sharepoint(
    current_user: UserInfo = Depends(get_current_user),
):
    """Full reindex of all configured SharePoint sites."""
    from app.services.ingestion_worker import ingestion_worker, SyncJob, JobType

    workspace_id = settings.effective_tenant_id or getattr(current_user, "azure_id", str(current_user.id))
    enqueued = []
    for site_url in settings.sharepoint_site_list:
        job = SyncJob(
            id=str(uuid.uuid4()),
            job_type=JobType.REINDEX,
            connector_type="sharepoint",
            source_id=site_url,
            workspace_id=workspace_id,
        )
        ingestion_worker.enqueue(job)
        enqueued.append({"job_id": job.id, "site": site_url})

    return {"message": f"Reindex queued for {len(enqueued)} sites", "jobs": enqueued}


@router.post("/org_website/reindex")
async def reindex_org_website(
    current_user: UserInfo = Depends(get_current_user),
):
    """Full recrawl of all configured organisation website domains."""
    from app.services.ingestion_worker import ingestion_worker, SyncJob, JobType

    workspace_id = settings.effective_tenant_id or getattr(current_user, "azure_id", str(current_user.id))
    enqueued = []
    for domain in settings.org_website_domains:
        job = SyncJob(
            id=str(uuid.uuid4()),
            job_type=JobType.REINDEX,
            connector_type="org_website",
            source_id=domain,
            workspace_id=workspace_id,
        )
        ingestion_worker.enqueue(job)
        enqueued.append({"job_id": job.id, "domain": domain})

    return {"message": f"Recrawl queued for {len(enqueued)} domains", "jobs": enqueued}


@router.get("/jobs")
async def list_jobs(
    connector_type: Optional[str] = None,
    current_user: UserInfo = Depends(get_current_user),
):
    """List recent sync jobs."""
    from app.services.ingestion_worker import ingestion_worker

    jobs = ingestion_worker.list_jobs(connector_type)[:50]
    return {
        "jobs": [
            {
                "id": j.id,
                "connector_type": j.connector_type,
                "job_type": j.job_type,
                "source_id": j.source_id,
                "status": j.status,
                "attempts": j.attempts,
                "docs_processed": j.docs_processed,
                "created_at": j.created_at.isoformat() if j.created_at else None,
                "finished_at": j.finished_at.isoformat() if j.finished_at else None,
                "error": j.error,
            }
            for j in jobs
        ]
    }


@router.get("/index/status")
async def index_status(
    current_user: UserInfo = Depends(get_current_user),
):
    """Return document counts for all Azure AI Search indexes."""
    try:
        from app.services.search.index_manager import index_manager

        indexes = [
            settings.AZURE_SEARCH_INDEX_NAME,
            settings.AZURE_SEARCH_VECTOR_INDEX_NAME,
            settings.AZURE_SEARCH_CACHE_INDEX_NAME,
        ]
        return {
            "indexes": [
                index_manager.get_index_stats(idx) if index_manager else {"index_name": idx, "error": "unavailable"}
                for idx in indexes
            ]
        }
    except Exception as exc:
        logger.error("Index status error: %s", str(exc))
        raise HTTPException(status_code=500, detail="Failed to retrieve index status")


@router.post("/onedrive/sync", response_model=JobResponse)
async def trigger_onedrive_sync(
    req: OneDriveSyncRequest,
    current_user: UserInfo = Depends(get_current_user),
):
    """Enqueue an app-only OneDrive sync for the authenticated user.

    No delegated token required — sync runs via client-credentials (app-only),
    which is safe for background/scheduled use.
    """
    from app.services.ingestion_worker import ingestion_worker, SyncJob, JobType

    user_id = getattr(current_user, "azure_id", None) or str(current_user.id)
    workspace_id = settings.effective_tenant_id or user_id
    delta_key = f"onedrive:{user_id}"

    job = SyncJob(
        id=str(uuid.uuid4()),
        job_type=JobType.FULL_SYNC if req.full_sync else JobType.DELTA_SYNC,
        connector_type="onedrive",
        source_id=delta_key,
        workspace_id=workspace_id,
        context_type="personal",
        user_id=user_id,
    )
    job_id = ingestion_worker.enqueue(job)
    return JobResponse(
        job_id=job_id, status="queued",
        connector_type="onedrive", source_id=user_id,
    )


@router.post("/onedrive/sync-all", response_model=List[JobResponse])
async def trigger_onedrive_sync_all(
    full_sync: bool = False,
    current_user: UserInfo = Depends(get_current_user),
):
    """Admin-only: enqueue an app-only OneDrive delta sync for all known users.

    Reads the list of users who have previously synced from the DB (those that
    have a persisted delta token) and queues a job for each.
    """
    if "Admin" not in (current_user.roles or []) and "admin" not in (current_user.roles or []):
        raise HTTPException(status_code=403, detail="Admin role required")

    from app.services.ingestion_worker import ingestion_worker, SyncJob, JobType
    from app.core.database import async_session_maker, db_available
    from app.models.models import ConnectorState
    from sqlalchemy import select

    if not db_available:
        raise HTTPException(status_code=503, detail="Database unavailable")

    async with async_session_maker() as db:
        stmt = select(ConnectorState).where(
            ConnectorState.connector_type == "onedrive",
            ConnectorState.state_key == "delta_token",
        )
        result = await db.execute(stmt)
        rows = result.scalars().all()

    workspace_id = settings.effective_tenant_id or "default"
    jobs_out = []
    for row in rows:
        user_id = row.source_id.removeprefix("onedrive:")
        if not user_id:
            continue
        job = SyncJob(
            id=str(uuid.uuid4()),
            job_type=JobType.FULL_SYNC if full_sync else JobType.DELTA_SYNC,
            connector_type="onedrive",
            source_id=row.source_id,
            workspace_id=workspace_id,
            context_type="personal",
            user_id=user_id,
        )
        ingestion_worker.enqueue(job)
        jobs_out.append(
            JobResponse(job_id=job.id, status="queued", connector_type="onedrive", source_id=user_id)
        )

    return jobs_out


@router.post("/sharepoint/crawl-rules")
async def set_sharepoint_crawl_rules(
    req: CrawlRuleRequest,
    current_user: UserInfo = Depends(get_current_user),
):
    """Set include/exclude rules for SharePoint crawling.

    - ``include_libraries``: Only crawl libraries whose names are in this list.
    - ``exclude_paths``: Skip items whose path contains any of these substrings.
    """
    # Store in settings for the current process; in production these would
    # be persisted to DB/config.
    if req.include_libraries is not None:
        settings._sharepoint_include_libraries = req.include_libraries
    if req.exclude_paths is not None:
        settings._sharepoint_exclude_paths = req.exclude_paths
    return {
        "include_libraries": getattr(settings, "_sharepoint_include_libraries", None),
        "exclude_paths": getattr(settings, "_sharepoint_exclude_paths", None),
    }


@router.get("/sharepoint/crawl-rules")
async def get_sharepoint_crawl_rules(
    current_user: UserInfo = Depends(get_current_user),
):
    return {
        "include_libraries": getattr(settings, "_sharepoint_include_libraries", None),
        "exclude_paths": getattr(settings, "_sharepoint_exclude_paths", None),
    }


# ── Legacy CRUD (kept for backward compat with existing Connector DB model) ───

@router.get("/", response_model=List[Dict[str, Any]])
async def list_connectors(
    current_user: UserInfo = Depends(get_current_user),
):
    """Return connector list (legacy endpoint — use /status for live data)."""
    return []
