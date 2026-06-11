"""FastAPI application entrypoint."""
from __future__ import annotations

import asyncio
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from .config import get_settings
from .database import session_scope
from .logging_config import setup_logging
from .middleware import (
    CSRFMiddleware, RateLimitMiddleware, RequestContextMiddleware,
    SecurityHeadersMiddleware,
)
from .routers import (
    admin_security, admin_tenant, agent_tokens, auth, connections, excel, health, mela, planner, privacy, scans, settings, tasks,
)
from .services.queue.queue import get_queue

logger = logging.getLogger(__name__)


async def _inproc_worker() -> None:
    """When QUEUE_PROVIDER=memory we run the scan worker inside the API
    process so that the dev quick-start truly works end to end with one
    `uvicorn` command. In production set QUEUE_PROVIDER=servicebus and run
    `python -m app.workers.worker` as a separate Web App."""
    from .services.tasks.scan_runner import run_scan
    queue = get_queue()
    logger.info("In-process worker starting (QUEUE_PROVIDER=memory)")
    async for payload in queue.consume():
        if payload.get("type") != "scan":
            continue
        try:
            async with session_scope() as session:
                await run_scan(session, payload["scan_run_id"])
        except Exception:
            logger.exception("In-process worker error")


@asynccontextmanager
async def lifespan(app: FastAPI):
    s = get_settings()
    task: asyncio.Task | None = None
    sched = None
    if s.queue_provider == "memory":
        task = asyncio.create_task(_inproc_worker())
        # In dev, also run the cadence scheduler in-process so a single
        # `uvicorn` command gives you the full automatic flow.
        try:
            from apscheduler.schedulers.asyncio import AsyncIOScheduler
            from .scheduler.scheduler import (
                SCAN_HOURS_LOCAL, _tick, warmup_pass,
            )
            sched = AsyncIOScheduler()
            sched.add_job(_tick, "cron", second=0)
            sched.start()
            logger.info(
                "In-process scheduler started (cadence CT hours: %s)",
                ", ".join(f"{h:02d}" for h in SCAN_HOURS_LOCAL),
            )
            asyncio.create_task(warmup_pass())
        except Exception:
            logger.exception("Failed to start in-process scheduler")
    try:
        yield
    finally:
        if sched is not None:
            try:
                sched.shutdown(wait=False)
            except Exception:
                pass
        if task and not task.done():
            task.cancel()
            try:
                await task
            except (asyncio.CancelledError, Exception):
                pass


def _validate_production_security(s) -> None:
    """Refuse to boot in production with insecure auth settings.

    These checks would have stopped a real-world incident class on their own:
    insecure cookies on HTTPS deployments, weak/leaked JWT secrets carried over
    from local dev, and SameSite=None without Secure (which browsers now refuse
    to set anyway).
    """
    if s.app_env != "production":
        return
    problems: list[str] = []
    if not s.cookie_secure:
        problems.append("COOKIE_SECURE must be true in production (HTTPS).")
    if s.cookie_samesite == "none" and not s.cookie_secure:
        problems.append("cookie_samesite=none requires cookie_secure=true.")
    if not s.jwt_secret or len(s.jwt_secret) < 32:
        problems.append("JWT_SECRET must be at least 32 characters in production.")
    weak = {"changeme", "secret", "dev", "test", "password"}
    if s.jwt_secret.lower() in weak:
        problems.append("JWT_SECRET appears to be a placeholder value.")
    if not s.secret_key or len(s.secret_key) < 32:
        problems.append("SECRET_KEY must be at least 32 characters in production.")
    if not s.token_encryption_key:
        problems.append("TOKEN_ENCRYPTION_KEY must be set in production.")
    if not s.csrf_enabled:
        problems.append("CSRF_ENABLED must be true in production.")
    if not s.rate_limit_enabled:
        problems.append("RATE_LIMIT_ENABLED must be true in production.")
    if problems:
        raise RuntimeError(
            "Refusing to start in production due to insecure config:\n  - "
            + "\n  - ".join(problems)
        )


def create_app() -> FastAPI:
    setup_logging()
    s = get_settings()
    _validate_production_security(s)
    app = FastAPI(title="Mela Task Radar API", version="0.1.0", lifespan=lifespan)

    # Middleware order matters: Starlette wraps in reverse, so the first
    # add runs LAST on the request and FIRST on the response. We want:
    #   request:  RequestContext → CORS → SecurityHeaders → RateLimit → CSRF → app
    # so RequestContext is added LAST (outermost).
    app.add_middleware(SecurityHeadersMiddleware, hsts=s.app_env == "production")
    app.add_middleware(RateLimitMiddleware, enabled=s.rate_limit_enabled)
    app.add_middleware(
        CSRFMiddleware,
        enabled=s.csrf_enabled,
        cookie_secure=s.cookie_secure,
        cookie_domain=s.cookie_domain,
    )
    app.add_middleware(
        CORSMiddleware,
        allow_origins=[s.frontend_url],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*", "X-CSRF-Token"],
        expose_headers=["X-CSRF-Token", "X-Request-ID"],
    )
    app.add_middleware(RequestContextMiddleware)

    app.include_router(health.router)
    app.include_router(auth.router, prefix="/api", tags=["auth"])
    app.include_router(connections.router, prefix="/api", tags=["connections"])
    app.include_router(settings.router, prefix="/api", tags=["settings"])
    app.include_router(scans.router, prefix="/api", tags=["scans"])
    app.include_router(tasks.router, prefix="/api", tags=["tasks"])
    app.include_router(excel.router, prefix="/api", tags=["excel"])
    app.include_router(planner.router, prefix="/api", tags=["planner"])
    app.include_router(mela.router, prefix="/api", tags=["mela"])
    app.include_router(agent_tokens.router, prefix="/api", tags=["agent-tokens"])
    app.include_router(admin_tenant.router, prefix="/api", tags=["admin"])
    app.include_router(admin_security.router, prefix="/api", tags=["admin"])
    app.include_router(privacy.router, prefix="/api", tags=["privacy"])
    return app


app = create_app()
