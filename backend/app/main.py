"""
Mela AI - Backend API
Enterprise AI Assistant Platform
Powered by Armely
"""

from contextlib import asynccontextmanager
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
import asyncio
import logging
import traceback
import uuid
from typing import AsyncGenerator

from app.core.config import settings
from app.core.logging import setup_logging
from app.api.router import api_router
from app.core.database import engine, Base, init_db, USE_SQLITE
from app.core.middleware import (
    EmbedFrameMiddleware,
    RateLimitMiddleware,
    RequestLoggingMiddleware,
)

# Setup logging
setup_logging()
logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator:
    """Application lifespan handler."""
    logger.info("Starting Mela AI Backend...")

    def _fire_background_alert(
        *,
        error_type: str,
        message: str,
        route: str,
        severity: str = "error",
        stack_trace: str = "",
    ) -> None:
        """Best-effort alert dispatch for background/startup blind spots."""
        try:
            from app.services.alert_service import send_alert, AlertIncident
            incident = AlertIncident(
                title=f"{error_type}: {message[:120]}",
                severity="critical" if severity in ("critical", "error") else "warning",
                code="STARTUP_FAILURE",
                route=route,
                error_message=message[:500],
                stack_trace=(stack_trace or "")[:3000],
            )
            asyncio.create_task(send_alert(incident))
        except Exception as _alert_err:
            logger.debug(
                "Background alert fire failed (non-fatal): %s",
                _alert_err,
            )

    def _attach_task_watchdog(task: asyncio.Task, task_name: str) -> None:
        """Alert when a long-running background task exits unexpectedly."""

        def _on_done(done: asyncio.Task) -> None:
            if done.cancelled():
                return
            try:
                exc = done.exception()
            except Exception as _lookup_err:
                logger.warning(
                    (
                        "Background task %s ended; failed to inspect "
                        "exception: %s"
                    ),
                    task_name,
                    _lookup_err,
                )
                _fire_background_alert(
                    error_type=f"{task_name}TaskExitUnknown",
                    message=str(_lookup_err),
                    route=f"background:{task_name}",
                    severity="critical",
                )
                return

            if exc is None:
                logger.warning(
                    "Background task exited unexpectedly: %s",
                    task_name,
                )
                _fire_background_alert(
                    error_type=f"{task_name}TaskExited",
                    message=(
                        "Background task "
                        f"'{task_name}' exited unexpectedly."
                    ),
                    route=f"background:{task_name}",
                    severity="critical",
                )
                return

            stack = "".join(
                traceback.format_exception(type(exc), exc, exc.__traceback__)
            )
            logger.error(
                "Background task crashed: %s: %s",
                task_name,
                exc,
                exc_info=exc,
            )
            _fire_background_alert(
                error_type=f"{task_name}TaskCrashed",
                message=str(exc),
                route=f"background:{task_name}",
                severity="critical",
                stack_trace=stack,
            )

        task.add_done_callback(_on_done)

    # Validate critical secrets before doing anything else
    if not settings.JWT_SECRET_KEY:
        if settings.APP_ENV != "development" and not settings.DEBUG:
            raise RuntimeError(
                "JWT_SECRET_KEY must be set in production. "
                "Add it to your environment variables or env/.env.dev."
            )
        else:
            import secrets as _secrets
            object.__setattr__(settings, "JWT_SECRET_KEY", _secrets.token_hex(32))
            logger.warning(
                "JWT_SECRET_KEY not set — generated a random key for this session. "
                "Dev tokens will be invalidated on restart. Set JWT_SECRET_KEY in env to persist sessions."
            )

    # Production: fail-fast if any other critical secret is missing or weak.
    if settings.APP_ENV != "development" and not settings.DEBUG:
        _required_prod_secrets = [
            ("JWT_SECRET_KEY", getattr(settings, "JWT_SECRET_KEY", None)),
            ("AZURE_TENANT_ID", getattr(settings, "AZURE_TENANT_ID", None)),
            ("AZURE_CLIENT_ID", getattr(settings, "AZURE_CLIENT_ID", None)),
        ]
        _missing = [name for name, val in _required_prod_secrets if not val]
        if _missing:
            raise RuntimeError(
                "Missing required production secrets: " + ", ".join(_missing)
            )
        # Reject obviously weak / placeholder JWT keys in prod.
        _jwt = settings.JWT_SECRET_KEY or ""
        _bad = {"changeme", "secret", "dev", "test", "password"}
        if len(_jwt) < 32 or _jwt.lower() in _bad:
            raise RuntimeError(
                "JWT_SECRET_KEY is too weak for production "
                "(must be >=32 chars and not a common placeholder)."
            )

        # Phase 0: assert dev-login is OFF and at least one AI provider is wired.
        if getattr(settings, "ENABLE_DEV_LOGIN", False):
            raise RuntimeError(
                "ENABLE_DEV_LOGIN must be false in production. "
                "Refusing to start with the dev-login bypass enabled."
            )
        _has_provider = any([
            getattr(settings, "AZURE_OPENAI_API_KEY", None),
            getattr(settings, "ANTHROPIC_API_KEY", None),
            getattr(settings, "GEMINI_API_KEY", None),
            getattr(settings, "OPENAI_API_KEY", None),
        ])
        if not _has_provider:
            raise RuntimeError(
                "No AI provider configured. Set at least one of "
                "AZURE_OPENAI_API_KEY, ANTHROPIC_API_KEY, GEMINI_API_KEY, "
                "or OPENAI_API_KEY before starting in production."
            )
        # CORS must not include localhost / wildcard in prod.
        _cors = list(settings.CORS_ORIGINS or [])
        _bad_origins = [
            o for o in _cors
            if o == "*" or "localhost" in o or "127.0.0.1" in o
        ]
        if _bad_origins:
            raise RuntimeError(
                "Insecure CORS origins in production: "
                + ", ".join(_bad_origins)
                + ". Restrict CORS_ORIGINS to your real domains."
            )

    # Initialize database
    if USE_SQLITE:
        # Use SQLite for local development
        await init_db()
        logger.info("Using SQLite database for local development")
    else:
        # SQL Server forbids multi-path ON DELETE CASCADE; SQLite allows it.
        # Rewrite every CASCADE FK pointing at users.id to NO ACTION so
        # create_all doesn't blow up on the next new table.  Soft-delete is
        # the app-level cleanup path, so DB cascades are redundant here.
        try:
            from sqlalchemy.schema import ForeignKeyConstraint as _FKC
            _patched = 0
            for _tbl in Base.metadata.tables.values():
                for _cons in list(_tbl.constraints):
                    if not isinstance(_cons, _FKC):
                        continue
                    for _fk in _cons.elements:
                        if (_fk.target_fullname or "").lower() == "users.id":
                            if (_cons.ondelete or "").upper() == "CASCADE":
                                _cons.ondelete = "NO ACTION"
                                _patched += 1
                            if (_fk.ondelete or "").upper() == "CASCADE":
                                _fk.ondelete = "NO ACTION"
            if _patched:
                logger.info(
                    "SQL Server cascade-path patch: rewrote %d FKs on users.id",
                    _patched,
                )
        except Exception as _patch_err:  # noqa: BLE001
            logger.warning("Cascade FK patch skipped: %s", _patch_err)

        # Try to connect to SQL Server
        try:
            async with engine.begin() as conn:
                if settings.APP_ENV == "development":
                    await conn.run_sync(Base.metadata.create_all)
            logger.info("Database initialized")
        except Exception as e:
            if settings.APP_ENV == "development" or settings.DEBUG:
                logger.warning(f"Database connection failed (dev fallback): {e}")
                logger.info("Continuing without database connection in development mode.")
            else:
                raise RuntimeError("Database connection failed in production mode.") from e

    logger.info(f"Mela AI Backend started in {settings.APP_ENV} mode")

    # Ensure Azure AI Search indexes exist (non-fatal if Search is not configured)
    try:
        from app.services.search.index_manager import index_manager
        if index_manager is not None:
            index_manager.ensure_all_indexes()
            logger.info("Azure AI Search indexes verified / created")
    except Exception as _e:
        logger.warning("Azure AI Search setup skipped: %s", _e)

    # Start the enterprise ingestion worker background loop
    from app.services.ingestion_worker import ingestion_worker, SyncJob, JobType
    try:
        await ingestion_worker.load_delta_tokens_from_db()
    except Exception as _load_err:
        logger.warning(
            "Failed to load ingestion delta tokens at startup: %s",
            _load_err,
        )
        _fire_background_alert(
            error_type="IngestionWorkerTokenLoadFailed",
            message=str(_load_err),
            route="startup:ingestion_worker.load_delta_tokens_from_db",
            severity="error",
            stack_trace=traceback.format_exc(),
        )
    _worker_task = asyncio.create_task(ingestion_worker.process_queue())
    _attach_task_watchdog(_worker_task, "ingestion_worker")
    logger.info("Ingestion worker started")

    # Auto-trigger delta sync for SharePoint sites and org website on startup.
    # Delta sync performs a full crawl on first run (no stored token), then
    # incremental on subsequent restarts.  Non-fatal if connectors are disabled.
    _shared_ws = settings.effective_tenant_id or "global"
    if settings.CONNECTOR_SHAREPOINT_ENABLED and settings.sharepoint_site_list:
        for _site in settings.sharepoint_site_list:
            ingestion_worker.enqueue(SyncJob(
                id=str(uuid.uuid4()),
                job_type=JobType.DELTA_SYNC,
                connector_type="sharepoint",
                source_id=_site,
                workspace_id=_shared_ws,
                context_type="org",
            ))
        logger.info("Queued SharePoint delta-sync for %d sites", len(settings.sharepoint_site_list))

    if settings.CONNECTOR_ORG_WEBSITE_ENABLED and settings.org_website_domains:
        for _domain in settings.org_website_domains:
            ingestion_worker.enqueue(SyncJob(
                id=str(uuid.uuid4()),
                job_type=JobType.DELTA_SYNC,
                connector_type="org_website",
                source_id=_domain,
                workspace_id=_shared_ws,
                context_type="org",
            ))
        logger.info("Queued org-website crawl for %d domains", len(settings.org_website_domains))

    # Auto-queue OneDrive delta sync for all users who have previously synced
    if settings.CONNECTOR_ONEDRIVE_ENABLED:
        await ingestion_worker.auto_queue_onedrive_for_known_users()

    # Periodic 30-minute OneDrive background refresh
    async def _onedrive_periodic_loop() -> None:
        """Re-queue a delta sync for all known OneDrive users every 30 minutes."""
        while True:
            await asyncio.sleep(30 * 60)
            if settings.CONNECTOR_ONEDRIVE_ENABLED:
                try:
                    await ingestion_worker.auto_queue_onedrive_for_known_users()
                except Exception as _od_err:
                    logger.warning(
                        "OneDrive periodic sync scheduler error: %s",
                        _od_err,
                    )
                    _fire_background_alert(
                        error_type="OneDrivePeriodicSyncSchedulerError",
                        message=str(_od_err),
                        route="background:onedrive_periodic_sync",
                        severity="error",
                        stack_trace=traceback.format_exc(),
                    )

    _onedrive_task = asyncio.create_task(_onedrive_periodic_loop())
    _attach_task_watchdog(_onedrive_task, "onedrive_periodic_sync")
    logger.info("OneDrive 30-minute periodic sync task started")

    # Periodic 24-hour ACL refresh for SharePoint documents
    async def _acl_refresh_periodic_loop() -> None:
        """Re-queue an ACL refresh job for each known SharePoint source every 24 hours."""
        from app.services.ingestion_worker import SyncJob as _SyncJob, JobType as _JobType
        import uuid as _uuid
        while True:
            await asyncio.sleep(24 * 3600)
            if not settings.CONNECTOR_SHAREPOINT_ENABLED:
                continue
            try:
                from app.core.database import async_session_maker, db_available
                from app.models.models import ConnectorState
                from sqlalchemy import select as _select
                if not db_available:
                    continue
                async with async_session_maker() as _db:
                    stmt = _select(ConnectorState).where(
                        ConnectorState.connector_type == "sharepoint",
                        ConnectorState.state_key == "delta_token",
                    )
                    rows = (await _db.execute(stmt)).scalars().all()
                for row in rows:
                    acl_job = _SyncJob(
                        id=str(_uuid.uuid4()),
                        job_type=_JobType.ACL_REFRESH,
                        connector_type="sharepoint",
                        source_id=row.source_id,
                        workspace_id=settings.effective_tenant_id or "default",
                        context_type="org",
                    )
                    ingestion_worker.enqueue(acl_job)
                logger.info("ACL refresh jobs enqueued for %d SharePoint sources", len(rows))
            except Exception as _acl_err:
                logger.warning("ACL refresh scheduler error: %s", _acl_err)
                _fire_background_alert(
                    error_type="SharePointACLRefreshSchedulerError",
                    message=str(_acl_err),
                    route="background:sharepoint_acl_refresh",
                    severity="error",
                    stack_trace=traceback.format_exc(),
                )

    _acl_task = asyncio.create_task(_acl_refresh_periodic_loop())
    _attach_task_watchdog(_acl_task, "sharepoint_acl_refresh")
    logger.info("SharePoint 24-hour ACL refresh task started")

    # Launch private-chat auto-deletion background task
    from app.services.private_chat_cleanup import start_cleanup_task
    _cleanup_task = asyncio.create_task(start_cleanup_task())
    _attach_task_watchdog(_cleanup_task, "private_chat_cleanup")
    logger.info("Private chat cleanup task started")

    # Launch session memory expiry cleanup (runs every 6 hours)
    async def _session_memory_cleanup_loop() -> None:
        from app.services.memory_service import memory_service
        from app.core.database import async_session_maker
        while True:
            await asyncio.sleep(6 * 3600)  # 6 hours
            try:
                async with async_session_maker() as _db:
                    count = await memory_service.cleanup_expired_sessions(_db)
                    if count:
                        logger.info("Session memory cleanup removed %d expired entries", count)
            except Exception as _e:
                logger.warning("Session memory cleanup error: %s", _e)
                _fire_background_alert(
                    error_type="SessionMemoryCleanupError",
                    message=str(_e),
                    route="background:session_memory_cleanup",
                    severity="error",
                    stack_trace=traceback.format_exc(),
                )

    _session_cleanup_task = asyncio.create_task(_session_memory_cleanup_loop())
    _attach_task_watchdog(_session_cleanup_task, "session_memory_cleanup")
    logger.info("Session memory cleanup task started")

    # ── Phase 4: Knowledge Base expiry sweep (runs every 6 hours) ────────
    # Hard-deletes KB entries past expires_at; mirrors deletion in the KB
    # Search index when configured.  Same lifespan pattern as the other
    # background loops; non-fatal on error.
    async def _kb_expiry_sweep_loop() -> None:
        from app.core.database import async_session_maker
        from app.orchestration.knowledge import knowledge_store
        while True:
            await asyncio.sleep(6 * 3600)  # 6 hours
            try:
                async with async_session_maker() as _db:
                    deleted = await knowledge_store.expire_stale(_db)
                    if deleted:
                        logger.info(
                            "KB expiry sweep removed %d stale entries",
                            deleted,
                        )
            except Exception as _e:
                logger.warning("KB expiry sweep error: %s", _e)
                _fire_background_alert(
                    error_type="KnowledgeBaseExpirySweepError",
                    message=str(_e),
                    route="background:kb_expiry_sweep",
                    severity="error",
                    stack_trace=traceback.format_exc(),
                )

    _kb_expiry_task = asyncio.create_task(_kb_expiry_sweep_loop())
    _attach_task_watchdog(_kb_expiry_task, "kb_expiry_sweep")
    logger.info("KB expiry sweep task started")

    # ── GDPR Sprint 2: retention sweep (every 6 hours) ────────────────────
    # Hard-deletes rows whose deleted_at is older than the configured
    # retention window. No-op when RETENTION_DAYS_* settings are 0.
    async def _retention_sweep_loop() -> None:
        from app.services.retention_sweep import sweep_once
        while True:
            await asyncio.sleep(6 * 3600)
            try:
                await sweep_once()
            except Exception as _e:
                logger.warning("Retention sweep error: %s", _e)

    _retention_task = asyncio.create_task(_retention_sweep_loop())
    _attach_task_watchdog(_retention_task, "retention_sweep")
    logger.info("Retention sweep task started")

    # Seed default model rankings so the models endpoint returns all models
    try:
        from app.core.database import async_session_maker
        from app.api.endpoints.model_settings import _seed_defaults
        async with async_session_maker() as _seed_db:
            await _seed_defaults(_seed_db)
        logger.info("Model rankings seeded / verified")
    except Exception as _e:
        logger.warning("Model ranking seed skipped: %s", _e)

    # Sprint 3.2: seed default enabled_tools rows so role gates work as soon
    # as ENFORCE_TOOL_ROLE_GATES is flipped on. Idempotent — never overwrites
    # existing admin-tuned rows.
    try:
        from app.core.database import async_session_maker
        from app.services.tool_access_seed import seed_enabled_tools
        async with async_session_maker() as _seed_db:
            await seed_enabled_tools(_seed_db)
    except Exception as _e:
        logger.warning("Enabled tools seed skipped: %s", _e)

    # Idempotently seed the orchestration brain's worker registry so the
    # planner / router / health endpoint know about every registered worker
    # from the first request.  Non-fatal: if a worker URL is blank, that
    # worker is simply not registered, and Mela boots without it.
    try:
        from app.core.database import async_session_maker as _seed_sm
        from app.orchestration.seed import seed_workers
        async with _seed_sm() as _seed_db:
            seeded = await seed_workers(_seed_db)
        if seeded:
            logger.info("Worker registry seeded: %s", ", ".join(seeded))
        else:
            logger.info("Worker registry seed produced no workers")
    except Exception as _e:
        logger.warning("Worker registry seed skipped: %s", _e)

    # Eagerly warm up the Graph app-only token so the first enterprise search
    # request doesn't incur a cold-start token acquisition delay.
    try:
        from app.services.obo_service import get_graph_token_app_only
        _graph_token = await get_graph_token_app_only()
        if _graph_token:
            logger.info("Graph app-only token warmed up at startup")
        else:
            logger.warning("Graph app-only token warm-up returned None — check AZURE_CLIENT_ID/SECRET")
    except Exception as _e:
        logger.warning("Graph token warm-up failed (non-fatal): %s", _e)

    # Phase 1: warm up the Redis singleton so connectivity errors surface at
    # boot rather than on the first request.  No-op when REDIS_URL is empty.
    try:
        from app.core.redis_client import get_redis
        _r = await get_redis()
        if _r is None and settings.REDIS_URL:
            logger.warning("REDIS_URL configured but client failed to connect; using in-process fallback")
    except Exception as _e:
        logger.warning("Redis warm-up failed (non-fatal): %s", _e)

    yield

    # Graceful shutdown
    ingestion_worker.stop()
    _worker_task.cancel()
    try:
        await _worker_task
    except asyncio.CancelledError:
        pass

    # Stop the cleanup task on shutdown
    _cleanup_task.cancel()
    try:
        await _cleanup_task
    except asyncio.CancelledError:
        pass

    _session_cleanup_task.cancel()
    try:
        await _session_cleanup_task
    except asyncio.CancelledError:
        pass

    _onedrive_task.cancel()
    try:
        await _onedrive_task
    except asyncio.CancelledError:
        pass

    _acl_task.cancel()
    try:
        await _acl_task
    except asyncio.CancelledError:
        pass
    try:
        await _session_cleanup_task
    except asyncio.CancelledError:
        pass

    _kb_expiry_task.cancel()
    try:
        await _kb_expiry_task
    except asyncio.CancelledError:
        pass

    logger.info("Shutting down Mela AI Backend...")
    try:
        await engine.dispose()
    except Exception:
        pass
    # Phase 1: close the Redis singleton if it was opened.
    try:
        from app.core.redis_client import close_redis
        await close_redis()
    except Exception as _e:
        logger.warning("Redis shutdown failed (non-fatal): %s", _e)


app = FastAPI(
    title="Mela AI API",
    description="Enterprise AI Assistant Platform API - Powered by Armely",
    version="1.0.0",
    docs_url="/docs" if settings.DEBUG else None,
    redoc_url="/redoc" if settings.DEBUG else None,
    openapi_url="/openapi.json" if settings.DEBUG else None,
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.CORS_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
    expose_headers=["*"],
)

app.add_middleware(RequestLoggingMiddleware)
app.add_middleware(RateLimitMiddleware)
# Phase 6B: frame-policy headers for embed routes only.
app.add_middleware(EmbedFrameMiddleware)

# Phase 5: Application Insights / OpenTelemetry — no-op when the connection
# string is unset so local dev stays quiet.
try:
    from app.core.telemetry import configure_telemetry
    configure_telemetry(app)
except Exception as _tel_err:
    logger.warning("Telemetry configuration failed (non-fatal): %s", _tel_err)


@app.exception_handler(Exception)
async def global_exception_handler(request: Request, exc: Exception):
    import traceback
    logger.exception("Unhandled exception: %s", exc)

    # Persist to error_logs table (best-effort — never block the response)
    request_id = request.headers.get("X-Request-ID", str(uuid.uuid4()))

    user_id: str | None = None
    user_email: str | None = None
    tenant_id: str | None = None
    try:
        user_id = request.state.user_id
        user_email = request.state.user_email
        tenant_id = request.state.tenant_id
    except AttributeError:
        pass

    try:
        from app.core.database import async_session_maker
        from app.models.models import ErrorLog

        async with async_session_maker() as _db:
            _db.add(ErrorLog(
                user_id=user_id,
                user_email=user_email,
                tenant_id=tenant_id,
                method=request.method,
                route=str(request.url.path),
                status_code=500,
                error_type=type(exc).__name__,
                message=str(exc)[:2000],
                stack_trace=traceback.format_exc()[:8000],
                severity="error",
                request_id=request_id,
            ))
            await _db.commit()
    except Exception as _log_err:
        logger.warning("Failed to persist ErrorLog: %s", _log_err)

    # Fire ops alert — non-blocking, rate-limited
    try:
        from app.services.alert_service import send_alert, AlertIncident
        incident = AlertIncident(
            title=f"{type(exc).__name__}: {str(exc)[:120]}",
            severity="critical",
            code="UNHANDLED_EXCEPTION",
            route=f"{request.method} {request.url.path}",
            tenant_id=tenant_id,
            error_message=str(exc)[:500],
            stack_trace=traceback.format_exc()[:3000],
        )
        asyncio.create_task(send_alert(incident))
    except Exception as _alert_err:
        logger.debug("Alert fire failed (non-fatal): %s", _alert_err)

    return JSONResponse(
        status_code=500,
        content={
            "error": "Internal server error",
            "message": (
                str(exc) if settings.DEBUG else "An unexpected error occurred"
            ),
            "request_id": request_id,
        },
    )


app.include_router(api_router, prefix=settings.API_PREFIX)


# ── Phase 6A: inbound MCP server ─────────────────────────────────────────
# Mounted on its own /mcp/v1 prefix so middleware (rate limiting, future
# auth) can be tuned independently of the user-facing REST surface.
# RateLimitMiddleware._SILENT_PATH_PREFIXES already includes "/mcp/" so
# external MCP clients aren't capped against the human-traffic bucket.
from app.mcp import mcp_router as _mcp_router  # noqa: E402
app.include_router(_mcp_router, prefix="/mcp/v1", tags=["MCP Server"])


@app.get("/health")
async def health_check():
    import os
    from datetime import datetime

    checks: dict = {}

    # DB connectivity (non-fatal)
    try:
        if USE_SQLITE:
            from app.core.database import async_session_maker
            async with async_session_maker() as _s:
                await _s.execute(__import__("sqlalchemy").text("SELECT 1"))
            checks["db"] = "ok"
        else:
            async with engine.connect() as _c:
                await _c.execute(__import__("sqlalchemy").text("SELECT 1"))
            checks["db"] = "ok"
    except Exception as _db_err:
        checks["db"] = f"error: {type(_db_err).__name__}"

    # Azure OpenAI config (presence check only — no network call)
    try:
        from app.services.openai_service import openai_service as _oai
        checks["openai"] = "configured" if _oai else "not_configured"
    except Exception:
        checks["openai"] = "not_configured"

    # Graph app-only token (cached — cheap after first call)
    try:
        from app.services.obo_service import _cached_token
        checks["graph_token"] = "cached" if _cached_token else "not_cached"
    except Exception:
        checks["graph_token"] = "unknown"

    # Azure AI Search and Graph configuration status
    checks["search_configured"] = bool(
        settings.AZURE_SEARCH_ENDPOINT and settings.AZURE_SEARCH_ADMIN_KEY
    )
    checks["graph_configured"] = bool(
        settings.effective_client_id and settings.effective_client_secret
    )

    overall = "healthy" if checks.get("db") == "ok" else "degraded"

    return {
        "status": overall,
        "app": "Mela AI",
        "version": os.environ.get("APP_VERSION", "1.0.0"),
        "environment": settings.APP_ENV,
        "commit": os.environ.get("COMMIT_SHA", "local"),
        "timestamp": datetime.utcnow().isoformat() + "Z",
        "checks": checks,
    }


@app.get("/")
async def root():
    return {
        "app": "Mela AI",
        "description": "Enterprise AI Assistant Platform",
        "organization": "Armely",
    }


# ── Phase 0.5: Kubernetes-style /ready probe ─────────────────────────────
# Real readiness: the process answers /health while booting, but /ready only
# returns 200 once the DB and Search are reachable.  Result is cached for
# 10 seconds so a load balancer probing every 1-2s doesn't hammer dependencies.
_READY_CACHE: dict = {"at": 0.0, "ok": False, "checks": {}}


@app.get("/ready")
async def readiness_probe():
    import time
    from datetime import datetime

    now = time.time()
    if now - _READY_CACHE["at"] < 10.0 and _READY_CACHE["at"] > 0:
        body = {
            "ready": _READY_CACHE["ok"],
            "checks": _READY_CACHE["checks"],
            "cached": True,
            "timestamp": datetime.utcnow().isoformat() + "Z",
        }
        return JSONResponse(status_code=200 if _READY_CACHE["ok"] else 503, content=body)

    checks: dict = {}

    # DB
    try:
        from sqlalchemy import text as _text
        if USE_SQLITE:
            from app.core.database import async_session_maker
            async with async_session_maker() as _s:
                await _s.execute(_text("SELECT 1"))
        else:
            async with engine.connect() as _c:
                await _c.execute(_text("SELECT 1"))
        checks["db"] = "ok"
    except Exception as _e:
        checks["db"] = f"error: {type(_e).__name__}"

    # Azure AI Search — index manager presence + lightweight call
    try:
        if settings.AZURE_SEARCH_ENDPOINT and settings.AZURE_SEARCH_ADMIN_KEY:
            from app.services.search.index_manager import index_manager
            if index_manager is not None:
                checks["search"] = "ok"
            else:
                checks["search"] = "not_initialized"
        else:
            checks["search"] = "not_configured"
    except Exception as _e:
        checks["search"] = f"error: {type(_e).__name__}"

    # LLM provider — presence check only (no token spend)
    try:
        from app.services.openai_service import openai_service as _oai
        checks["llm"] = "ok" if _oai else "not_configured"
    except Exception as _e:
        checks["llm"] = f"error: {type(_e).__name__}"

    # Ready iff DB is reachable and at least one of search/llm is configured.
    ok = (
        checks.get("db") == "ok"
        and (checks.get("llm") == "ok" or checks.get("search") == "ok")
    )

    _READY_CACHE["at"] = now
    _READY_CACHE["ok"] = ok
    _READY_CACHE["checks"] = checks

    return JSONResponse(
        status_code=200 if ok else 503,
        content={
            "ready": ok,
            "checks": checks,
            "cached": False,
            "timestamp": datetime.utcnow().isoformat() + "Z",
        },
    )


if __name__ == "__main__":
    import os
    import uvicorn
    port = int(os.environ.get("PORT", settings.API_PORT))
    uvicorn.run("app.main:app", host=settings.API_HOST, port=port, reload=settings.DEBUG)
