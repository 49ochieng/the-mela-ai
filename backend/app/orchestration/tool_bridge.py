"""
Mela AI - Capability-to-tool bridge.

Synthesises an OpenAI tool-function definition for every registered
worker capability and provides the dispatcher that turns a tool call
into a ``MelaTask`` → executor call.

Tool naming convention
----------------------

A worker capability ``get_overdue_tasks`` on worker ``task-radar``
becomes the tool ``worker__task_radar__get_overdue_tasks``.  The
double-underscore separator is unambiguous against the existing 18
built-in tools (which use single words like ``get_inbox``,
``run_python_code``).  Worker IDs containing ``-`` are slugified to
``_`` so the resulting name is a valid identifier the LLM can emit.

Personal-mode filter
--------------------

If a manifest sets ``auth_config["scope"] = "enterprise"``, every
capability from that worker is stripped in personal mode — same idea
as ``_BLOCKED_GRAPH_TOOLS`` in the existing tool_executor.  Workers
without an explicit scope default to ``enterprise`` to be safe; users
can call ``set_personal=True`` on the manifest's auth_config to opt
in to personal mode for a specific worker.
"""

from __future__ import annotations

import logging
from typing import Any, Optional

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.mode import UserSession
from app.orchestration.executor import executor as default_executor
from app.orchestration.executor import Executor, ProgressCallback
from app.orchestration.registry import worker_registry
from app.orchestration.types import (
    Capability,
    MelaContext,
    MelaTask,
    Priority,
    WorkerManifest,
)

logger = logging.getLogger(__name__)


# Separator between the literal "worker", worker_id, and capability.
# Two underscores so it can't collide with a worker_id or capability
# that itself contains a single underscore.
_SEP = "__"
_PREFIX = f"worker{_SEP}"


def _slug(s: str) -> str:
    """Slugify a worker_id for use inside an OpenAI tool name."""
    return s.replace("-", "_")


def synth_tool_name(worker_id: str, capability: str) -> str:
    """Build the tool name the LLM sees."""
    return f"{_PREFIX}{_slug(worker_id)}{_SEP}{capability}"


def parse_tool_name(tool_name: str) -> Optional[tuple[str, str]]:
    """Reverse :func:`synth_tool_name`. Returns (worker_id, capability) or None."""
    if not tool_name.startswith(_PREFIX):
        return None
    rest = tool_name[len(_PREFIX) :]
    parts = rest.split(_SEP, 1)
    if len(parts) != 2:
        return None
    worker_slug, capability = parts
    return worker_slug, capability


def _is_enterprise_only(manifest: WorkerManifest) -> bool:
    """Default-deny in personal mode — worker must opt in."""
    ac = manifest.auth_config or {}
    scope = ac.get("scope")
    if scope == "personal" or scope == "all":
        return False
    # explicit scope="enterprise" or no scope set → enterprise-only
    return True


def _capability_to_tool_def(
    manifest: WorkerManifest, capability: Capability
) -> dict[str, Any]:
    """Convert a Capability into an OpenAI tool-function dict."""
    name = synth_tool_name(manifest.id, capability.name)
    # Strip user_id/tenant_id from the JSON Schema we expose to the LLM —
    # those are overlaid from MelaContext, not chosen by the model.
    raw_params = dict(capability.input_params or {})
    if isinstance(raw_params.get("properties"), dict):
        props = dict(raw_params["properties"])
        props.pop("user_id", None)
        props.pop("tenant_id", None)
        raw_params["properties"] = props
    if isinstance(raw_params.get("required"), list):
        raw_params["required"] = [
            r for r in raw_params["required"] if r not in ("user_id", "tenant_id")
        ]
    if "type" not in raw_params:
        raw_params["type"] = "object"
    if "properties" not in raw_params:
        raw_params["properties"] = {}

    description = (
        f"[Worker: {manifest.display_name}] {capability.description}"
    )
    return {
        "type": "function",
        "function": {
            "name": name,
            "description": description,
            "parameters": raw_params,
        },
    }


async def synth_worker_tools(
    db: AsyncSession,
    *,
    user_session: Optional[UserSession] = None,
) -> list[dict[str, Any]]:
    """Return the OpenAI tool-function defs for every capability the
    current session is allowed to invoke.

    Layered filters:
      1. Personal/work scope — enterprise-only manifests stripped in
         personal mode (Phase 2).
      2. Tenant access (Phase 5C) — when the deployment is in
         default-deny mode, only workers the session's tenant has been
         granted access to are surfaced.  In default-allow mode this
         is a no-op (and skips the DB round-trip).
    """
    manifests = await worker_registry.list(db)
    is_personal = bool(user_session and user_session.is_personal)

    # ── Phase 2 personal filter ───────────────────────────────────────
    candidates = [
        m for m in manifests
        if not (is_personal and _is_enterprise_only(m))
    ]

    # ── Phase 5C tenant access filter ─────────────────────────────────
    from app.orchestration.access import allowed_worker_ids
    tenant_id = (
        user_session.tenant_id if user_session is not None else None
    )
    allowed = await allowed_worker_ids(
        db,
        tenant_id=tenant_id,
        candidate_ids=[m.id for m in candidates],
    )
    candidates = [m for m in candidates if m.id in allowed]

    tools: list[dict[str, Any]] = []
    for manifest in candidates:
        for cap in manifest.capabilities:
            tools.append(_capability_to_tool_def(manifest, cap))
    return tools


# ── Dispatch ─────────────────────────────────────────────────────────────


async def dispatch_worker_tool(
    db: AsyncSession,
    *,
    tool_name: str,
    arguments: dict[str, Any],
    user_id: str,
    tenant_id: Optional[str],
    trace_id: str,
    project_id: Optional[str] = None,
    on_progress: Optional[ProgressCallback] = None,
    executor: Executor | None = None,
) -> Optional[dict[str, Any]]:
    """Run one synthesised worker tool.  Returns the same shape every
    other handler in ``tool_executor`` returns: ``{"success": bool, ...}``.

    Returns ``None`` if *tool_name* is not a worker tool — caller falls
    through to the existing built-in tool dispatch.
    """
    parsed = parse_tool_name(tool_name)
    if parsed is None:
        return None
    worker_slug, capability = parsed

    manifests = await worker_registry.list(db)
    # Match by slugified ID so the LLM-emitted name maps back cleanly.
    target = next(
        (m for m in manifests if _slug(m.id) == worker_slug), None
    )
    if target is None:
        return {
            "success": False,
            "error": f"unknown worker: {worker_slug!r}",
            "tool": tool_name,
        }

    if not target.has_capability(capability):
        return {
            "success": False,
            "error": (
                f"capability {capability!r} not declared by worker {target.id!r}"
            ),
            "tool": tool_name,
        }

    cap_meta = target.capability(capability)
    is_async = bool(cap_meta and cap_meta.is_async)

    task = MelaTask(
        capability=capability,
        worker_id=target.id,
        params=dict(arguments or {}),
        context=MelaContext(
            tenant_id=tenant_id or "",
            user_id=user_id,
            project_id=project_id,
            priority=Priority.NORMAL,
        ),
        execution_mode="async" if is_async else "sync",
        trace_id=trace_id,
        timeout_ms=target.timeout_ms,
    )

    exec_ = executor or default_executor
    result = await exec_.run_single(
        db, task, trace_id=trace_id, goal=tool_name, on_progress=on_progress
    )

    # Map MelaResult → the tool-handler dict shape upstream consumers expect.
    out: dict[str, Any] = {
        "success": result.success,
        "summary": result.summary,
        "worker_id": result.worker_id,
        "capability": result.capability,
        "trace_id": result.trace_id,
        "task_id": result.task_id,
        "latency_ms": result.metadata.latency_ms,
    }
    if result.success:
        # Surface data — the LLM uses this to compose its answer.
        # Keep it inline so the chat history shows what came back.
        out["data"] = result.data
        if is_async:
            out["mode"] = "async"
            out["note"] = (
                "Worker accepted the job; the real result will arrive via "
                "/api/v1/ingest/result and surface as a notification."
            )
    else:
        err = result.error
        out["error"] = (err.message if err else "unknown adapter failure")
        out["error_code"] = (err.code if err else "UNKNOWN")
        out["retryable"] = bool(err and err.retryable)
    return out
