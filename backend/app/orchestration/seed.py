"""
Mela AI - Worker registry seeding.

Runs at app startup.  Idempotent by design: ``WorkerRegistry.upsert``
keys on ``manifest.id``, so calling ``seed_workers()`` on every boot
never creates duplicates and always converges the row to the
canonical manifest defined here.

Phase 1 seeds exactly one worker — Mela Task Radar — with all 9 of its
confirmed MCP capabilities.  Future workers each get their own
``_build_<worker>_manifest()`` and a corresponding entry in
``seed_workers()``.
"""

from __future__ import annotations

import logging
from typing import Optional

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.orchestration.registry import WorkerRegistry, worker_registry
from app.orchestration.types import (
    AuthScheme,
    Capability,
    Protocol,
    RetryPolicy,
    WorkerManifest,
    WorkerStatus,
)

logger = logging.getLogger(__name__)


# ── Capability definitions for Mela Task Radar ───────────────────────────
# Sourced from Task Radar's 9 confirmed MCP tools.  user_id and tenant_id
# are required on every call — the adapter overlays them from MelaContext.

_TASK_RADAR_CAPABILITIES: list[Capability] = [
    Capability(
        name="get_tasks",
        description="List tasks for the user, optionally filtered by status / project.",
        input_params={
            "type": "object",
            "properties": {
                "user_id": {"type": "string"},
                "tenant_id": {"type": "string"},
                "status": {"type": "string", "enum": ["open", "done", "all"]},
                "project_id": {"type": "string"},
                "limit": {"type": "integer", "minimum": 1, "maximum": 200},
            },
            "required": ["user_id", "tenant_id"],
        },
        output_shape={
            "type": "object",
            "properties": {"tasks": {"type": "array"}},
        },
        is_async=False,
        estimated_ms=400,
    ),
    Capability(
        name="get_task_detail",
        description="Fetch full details for a single task by id.",
        input_params={
            "type": "object",
            "properties": {
                "user_id": {"type": "string"},
                "tenant_id": {"type": "string"},
                "task_id": {"type": "string"},
            },
            "required": ["user_id", "tenant_id", "task_id"],
        },
        output_shape={"type": "object"},
        is_async=False,
        estimated_ms=300,
    ),
    Capability(
        name="get_scan_runs",
        description="List recent scan runs (mailbox / planner / etc.) with their status.",
        input_params={
            "type": "object",
            "properties": {
                "user_id": {"type": "string"},
                "tenant_id": {"type": "string"},
                "limit": {"type": "integer", "minimum": 1, "maximum": 100},
            },
            "required": ["user_id", "tenant_id"],
        },
        output_shape={"type": "object", "properties": {"scans": {"type": "array"}}},
        is_async=False,
        estimated_ms=400,
    ),
    Capability(
        name="trigger_scan",
        description=(
            "Kick off an asynchronous Task Radar scan. Returns immediately; the "
            "scan completes in the background and Task Radar POSTs the result "
            "to Mela's ingestion API."
        ),
        input_params={
            "type": "object",
            "properties": {
                "user_id": {"type": "string"},
                "tenant_id": {"type": "string"},
                "source": {
                    "type": "string",
                    "enum": ["mailbox", "planner", "all"],
                    "default": "all",
                },
            },
            "required": ["user_id", "tenant_id"],
        },
        output_shape={"type": "object", "properties": {"scan_id": {"type": "string"}}},
        is_async=True,
        estimated_ms=300,
    ),
    Capability(
        name="get_connections",
        description="List the user's connected Task Radar sources and their auth status.",
        input_params={
            "type": "object",
            "properties": {
                "user_id": {"type": "string"},
                "tenant_id": {"type": "string"},
            },
            "required": ["user_id", "tenant_id"],
        },
        output_shape={"type": "object"},
        is_async=False,
        estimated_ms=300,
    ),
    Capability(
        name="update_task_status",
        description="Mark a task as done / open / in-progress.",
        input_params={
            "type": "object",
            "properties": {
                "user_id": {"type": "string"},
                "tenant_id": {"type": "string"},
                "task_id": {"type": "string"},
                "status": {
                    "type": "string",
                    "enum": ["open", "in_progress", "done"],
                },
            },
            "required": ["user_id", "tenant_id", "task_id", "status"],
        },
        output_shape={"type": "object"},
        is_async=False,
        estimated_ms=300,
    ),
    Capability(
        name="get_tasks_today",
        description="List tasks due or scheduled for today.",
        input_params={
            "type": "object",
            "properties": {
                "user_id": {"type": "string"},
                "tenant_id": {"type": "string"},
            },
            "required": ["user_id", "tenant_id"],
        },
        output_shape={"type": "object", "properties": {"tasks": {"type": "array"}}},
        is_async=False,
        estimated_ms=350,
    ),
    Capability(
        name="get_overdue_tasks",
        description="List tasks past their due date that are still open.",
        input_params={
            "type": "object",
            "properties": {
                "user_id": {"type": "string"},
                "tenant_id": {"type": "string"},
            },
            "required": ["user_id", "tenant_id"],
        },
        output_shape={"type": "object", "properties": {"tasks": {"type": "array"}}},
        is_async=False,
        estimated_ms=350,
    ),
    Capability(
        name="get_audit_log",
        description="Return recent audit-log entries for the user / tenant.",
        input_params={
            "type": "object",
            "properties": {
                "user_id": {"type": "string"},
                "tenant_id": {"type": "string"},
                "limit": {"type": "integer", "minimum": 1, "maximum": 500},
            },
            "required": ["user_id", "tenant_id"],
        },
        output_shape={"type": "object", "properties": {"entries": {"type": "array"}}},
        is_async=False,
        estimated_ms=400,
    ),
]


def _build_task_radar_manifest() -> Optional[WorkerManifest]:
    base_url = (settings.TASK_RADAR_BASE_URL or "").strip()
    api_key = (settings.TASK_RADAR_MCP_API_KEY or "").strip()
    if not base_url:
        logger.info(
            "Task Radar seed skipped: TASK_RADAR_BASE_URL not configured"
        )
        return None

    health_url = base_url.rstrip("/") + "/health?deep=true"

    inbound_key = (settings.TASK_RADAR_INBOUND_API_KEY or "").strip()

    # Stamp the report_back_url from MELA_INGESTION_BASE_URL when set.
    # If blank, log a warning but DO NOT crash startup — Mela still serves
    # sync capabilities; only async callbacks lose their automatic route.
    ingest_base = (settings.MELA_INGESTION_BASE_URL or "").strip().rstrip("/")
    if ingest_base:
        report_back_url = f"{ingest_base}/api/v1/ingest/result"
    else:
        report_back_url = None
        logger.warning(
            "MELA_INGESTION_BASE_URL is not set — Task Radar manifest will "
            "be seeded with no report_back_url. Async worker callbacks "
            "won't auto-route until this is configured."
        )

    auth_config: dict[str, str] = {"header": "X-Api-Key"}
    if api_key:
        auth_config["api_key"] = api_key
    if inbound_key:
        # Used by require_worker_api_key on /api/v1/ingest/* callbacks.
        auth_config["inbound_api_key"] = inbound_key
    else:
        logger.warning(
            "TASK_RADAR_INBOUND_API_KEY is not set — /api/v1/ingest/* "
            "callbacks from Task Radar will be rejected. Set this env "
            "var to enable async result delivery."
        )
    # Default scope for Task Radar — enterprise-only by default
    # (excluded from personal-mode tool synthesis).  Override with
    # scope="all" if you ever expose it to personal mode.
    auth_config["scope"] = "enterprise"

    return WorkerManifest(
        id="task-radar",
        display_name="Mela Task Radar",
        version="1.0.0",
        capabilities=_TASK_RADAR_CAPABILITIES,
        protocol=Protocol.MCP,
        base_url=base_url,
        health_check_url=health_url,
        auth_scheme=AuthScheme.API_KEY,
        auth_config=auth_config,
        timeout_ms=30_000,
        # Two attempts max — circuit breaker is the fail-fast policy; we
        # don't want a fourth retry layer on top of model_router +
        # outcome_orchestrator + the breaker.
        retry_policy=RetryPolicy(
            max_attempts=2, backoff_ms=500, backoff_multiplier=2.0
        ),
        report_back_url=report_back_url,
        status=WorkerStatus.UNKNOWN,
    )


# ── Meeting Assistant capabilities (Phase 4) ─────────────────────────────
#
# Same MCP-over-HTTP shape as Task Radar.  Adapter resolution happens
# automatically via ``AdapterFactory`` keyed on ``manifest.protocol`` —
# no new adapter code is required for this worker; it shares MCPAdapter
# with Task Radar.

_MEETING_ASSISTANT_CAPABILITIES: list[Capability] = [
    Capability(
        name="get_meeting_summary",
        description="Return the summary for a specific meeting by id.",
        input_params={
            "type": "object",
            "properties": {
                "user_id": {"type": "string"},
                "tenant_id": {"type": "string"},
                "meeting_id": {"type": "string"},
            },
            "required": ["user_id", "tenant_id", "meeting_id"],
        },
        output_shape={"type": "object"},
        is_async=False,
        estimated_ms=600,
    ),
    Capability(
        name="get_action_items",
        description="List action items / decisions captured from a meeting.",
        input_params={
            "type": "object",
            "properties": {
                "user_id": {"type": "string"},
                "tenant_id": {"type": "string"},
                "meeting_id": {"type": "string"},
            },
            "required": ["user_id", "tenant_id", "meeting_id"],
        },
        output_shape={"type": "object"},
        is_async=False,
        estimated_ms=500,
    ),
    Capability(
        name="get_participants",
        description="List participants for a meeting (names + emails).",
        input_params={
            "type": "object",
            "properties": {
                "user_id": {"type": "string"},
                "tenant_id": {"type": "string"},
                "meeting_id": {"type": "string"},
            },
            "required": ["user_id", "tenant_id", "meeting_id"],
        },
        output_shape={"type": "object"},
        is_async=False,
        estimated_ms=400,
    ),
    Capability(
        name="get_past_meetings",
        description="List recent meetings for the user (most recent first).",
        input_params={
            "type": "object",
            "properties": {
                "user_id": {"type": "string"},
                "tenant_id": {"type": "string"},
                "limit": {"type": "integer", "minimum": 1, "maximum": 50},
            },
            "required": ["user_id", "tenant_id"],
        },
        output_shape={"type": "object"},
        is_async=False,
        estimated_ms=600,
    ),
    Capability(
        name="get_meeting_transcript",
        description=(
            "Return the full meeting transcript.  May be slower than other "
            "capabilities — budget accordingly."
        ),
        input_params={
            "type": "object",
            "properties": {
                "user_id": {"type": "string"},
                "tenant_id": {"type": "string"},
                "meeting_id": {"type": "string"},
            },
            "required": ["user_id", "tenant_id", "meeting_id"],
        },
        output_shape={"type": "object"},
        is_async=False,
        estimated_ms=3000,
    ),
    Capability(
        name="answer_meeting_question",
        description=(
            "Ask a natural-language question against the meeting transcript "
            "and return a grounded answer."
        ),
        input_params={
            "type": "object",
            "properties": {
                "user_id": {"type": "string"},
                "tenant_id": {"type": "string"},
                "meeting_id": {"type": "string"},
                "question": {"type": "string"},
            },
            "required": [
                "user_id", "tenant_id", "meeting_id", "question",
            ],
        },
        output_shape={"type": "object"},
        is_async=False,
        estimated_ms=5000,
    ),
]


def _build_meeting_assistant_manifest() -> WorkerManifest:
    """Always produces a manifest.

    When the worker isn't configured (blank URL), the manifest is still
    seeded so admins see it in the registry — but with
    ``status=unconfigured`` (a UX distinction from ``unreachable``,
    which means "configured but not responding").  The router will
    surface ``UNKNOWN_WORKER`` only if the row is removed, not if it
    has ``unconfigured`` status — capability validation is unaffected.
    """
    base_url = (settings.MEETING_ASSISTANT_BASE_URL or "").strip()
    api_key = (settings.MEETING_ASSISTANT_MCP_API_KEY or "").strip()
    inbound_key = (
        settings.MEETING_ASSISTANT_INBOUND_API_KEY or ""
    ).strip()
    is_configured = bool(base_url)

    health_url = (
        base_url.rstrip("/") + "/health?deep=true"
        if is_configured else ""
    )

    ingest_base = (settings.MELA_INGESTION_BASE_URL or "").strip().rstrip("/")
    report_back_url = (
        f"{ingest_base}/api/v1/ingest/result"
        if (ingest_base and is_configured) else None
    )

    auth_config: dict[str, str] = {
        "header": "X-Api-Key",
        "scope": "enterprise",
    }
    if api_key:
        auth_config["api_key"] = api_key
    if inbound_key:
        auth_config["inbound_api_key"] = inbound_key

    return WorkerManifest(
        id="meeting-assistant",
        display_name="Meeting Assistant",
        version="1.0.0",
        capabilities=_MEETING_ASSISTANT_CAPABILITIES,
        protocol=Protocol.MCP,
        base_url=base_url or "about:blank",
        health_check_url=health_url or "about:blank",
        auth_scheme=AuthScheme.API_KEY,
        auth_config=auth_config,
        timeout_ms=30_000,
        retry_policy=RetryPolicy(
            max_attempts=2, backoff_ms=500, backoff_multiplier=2.0
        ),
        report_back_url=report_back_url,
        status=(
            WorkerStatus.UNKNOWN if is_configured
            else WorkerStatus.UNCONFIGURED
        ),
    )


async def seed_workers(
    db: AsyncSession, *, registry: WorkerRegistry | None = None
) -> list[str]:
    """Idempotently seed all known workers. Returns the list of seeded ids."""
    reg = registry or worker_registry
    seeded: list[str] = []

    manifest = _build_task_radar_manifest()
    if manifest is not None:
        await reg.upsert(db, manifest)
        seeded.append(manifest.id)
        logger.info(
            "Worker registry: upserted %s with %d capabilities",
            manifest.id,
            len(manifest.capabilities),
        )

    # Meeting Assistant is ALWAYS seeded — even when blank-URL — so the
    # admin worker list shows it as ``unconfigured`` rather than
    # silently absent.  Same generic MCPAdapter; zero adapter code.
    meeting_manifest = _build_meeting_assistant_manifest()
    await reg.upsert(db, meeting_manifest)
    seeded.append(meeting_manifest.id)
    logger.info(
        "Worker registry: upserted %s with %d capabilities (status=%s)",
        meeting_manifest.id,
        len(meeting_manifest.capabilities),
        meeting_manifest.status.value,
    )

    return seeded
