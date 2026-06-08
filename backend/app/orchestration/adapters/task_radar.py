"""
Mela AI - MCP-over-HTTP adapter (canonical).

Translates a ``MelaTask`` into a single MCP tool invocation against any
worker that speaks the MCP-over-HTTP shape::

    POST {base_url}
    Headers: {auth header} (e.g. X-Api-Key)
    Body: {"tool": "<tool_name>", "arguments": {...}}

Any worker registered with ``protocol="mcp"`` is served by this adapter
— every new MCP worker added to the registry costs zero new adapter
code.  ``TaskRadarAdapter`` is kept as a back-compat alias.

Hard rules enforced here (do NOT relax):
  1. Every call passes EXPLICIT user_id and tenant_id from
     ``MelaTask.context``.  Task Radar (and likely future MCP workers)
     have known multi-user auto-resolution bugs; never let those keys
     be omitted from the arguments dict.
  2. The adapter never raises — failures become ``MelaResult.failure(...)``.
  3. ``execution_mode="async"`` (or capability declared async via
     ``manifest.auth_config["async_capabilities"]``) returns immediately;
     the worker reports completion via Mela's ingestion API.
"""

from __future__ import annotations

import logging
import time
from typing import Any, Optional

import httpx

from app.orchestration.adapters.base import AdapterHealth, WorkerAdapter
from app.orchestration.types import (
    MelaResult,
    MelaResultMetadata,
    MelaTask,
    WorkerManifest,
)

logger = logging.getLogger(__name__)


# Default async capabilities for legacy Task Radar registrations.  Workers
# can override by listing their async capabilities in
# ``manifest.auth_config["async_capabilities"]`` (json array).
_DEFAULT_ASYNC_CAPABILITIES = frozenset({"trigger_scan"})


class MCPAdapter(WorkerAdapter):
    """Generic MCP-over-HTTP adapter.

    Configurable per-worker via ``manifest.auth_config``:

      * ``api_key``       — token for the auth header (optional)
      * ``header``        — auth header name (default: ``X-Api-Key``)
      * ``async_capabilities`` — list[str] of capability names the worker
                            handles asynchronously.  Adapter returns a
                            success ``MelaResult`` immediately and the
                            real result lands via /ingest/result.
    """

    def __init__(self, manifest: WorkerManifest, **kwargs: Any) -> None:
        super().__init__(manifest, **kwargs)
        ac = manifest.auth_config or {}
        self._api_key: Optional[str] = ac.get("api_key")
        self._auth_header: str = ac.get("header") or "X-Api-Key"
        # Per-worker async capability override, with Task-Radar default.
        async_caps = ac.get("async_capabilities")
        if isinstance(async_caps, list) and async_caps:
            self._async_caps: frozenset[str] = frozenset(
                str(c) for c in async_caps
            )
        else:
            self._async_caps = _DEFAULT_ASYNC_CAPABILITIES
        if not self._api_key:
            logger.warning(
                "MCPAdapter[%s]: no api_key in manifest.auth_config — "
                "calls will be sent unauthenticated",
                manifest.id,
            )

    # ── Required base hooks ──────────────────────────────────────────────

    async def _dispatch(self, task: MelaTask) -> MelaResult:
        arguments = self._build_arguments(task)
        body = {"tool": task.capability, "arguments": arguments}
        timeout_s = task.timeout_ms / 1000.0
        started = time.monotonic()

        async with httpx.AsyncClient(timeout=timeout_s) as client:
            response = await client.post(
                self.manifest.base_url,
                json=body,
                headers=self._headers(task),
            )

        latency_ms = int((time.monotonic() - started) * 1000)

        if response.status_code >= 500:
            return self._failure(
                task,
                code="WORKER_5XX",
                message=f"{self.manifest.id} returned {response.status_code}: {response.text[:200]}",
                retryable=True,
                latency_ms=latency_ms,
            )
        if response.status_code == 429:
            return self._failure(
                task,
                code="WORKER_RATE_LIMITED",
                message=f"{self.manifest.id} returned 429",
                retryable=True,
                latency_ms=latency_ms,
            )
        if response.status_code >= 400:
            return self._failure(
                task,
                code=f"WORKER_{response.status_code}",
                message=f"{self.manifest.id} returned {response.status_code}: {response.text[:200]}",
                retryable=False,
                latency_ms=latency_ms,
            )

        try:
            payload = response.json()
        except ValueError as exc:
            return self._failure(
                task,
                code="INVALID_JSON",
                message=f"{self.manifest.id} returned non-JSON body: {exc}",
                retryable=False,
                latency_ms=latency_ms,
            )

        # Async capability — worker has accepted the job; result lands later
        # via /api/v1/ingest/result. Surface a success result immediately.
        if (
            task.capability in self._async_caps
            or task.execution_mode == "async"
        ):
            return MelaResult(
                task_id=task.task_id,
                trace_id=task.trace_id,
                worker_id=self.manifest.id,
                capability=task.capability,
                success=True,
                data={"accepted": True, "worker_response": payload},
                summary=f"{self.manifest.id} accepted async {task.capability}; awaiting callback",
                metadata=MelaResultMetadata(
                    latency_ms=latency_ms, source=self.manifest.id
                ),
            )

        return self._success(task, payload, latency_ms=latency_ms)

    async def health_check(self) -> AdapterHealth:
        url = self.manifest.health_check_url
        started = time.monotonic()
        try:
            async with httpx.AsyncClient(timeout=5.0) as client:
                resp = await client.get(url, headers=self._headers(None))
        except Exception as exc:  # noqa: BLE001 — health probe must not raise
            latency_ms = int((time.monotonic() - started) * 1000)
            return AdapterHealth(
                healthy=False,
                latency_ms=latency_ms,
                detail=f"{type(exc).__name__}: {exc}",
            )
        latency_ms = int((time.monotonic() - started) * 1000)
        if resp.status_code >= 500:
            return AdapterHealth(
                healthy=False,
                latency_ms=latency_ms,
                detail=f"HTTP {resp.status_code}",
            )
        return AdapterHealth(
            healthy=resp.status_code < 400,
            latency_ms=latency_ms,
            detail=f"HTTP {resp.status_code}",
        )

    def is_retryable(self, error: BaseException) -> bool:
        if isinstance(
            error,
            (
                httpx.TimeoutException,
                httpx.ConnectError,
                httpx.ReadError,
                httpx.RemoteProtocolError,
            ),
        ):
            return True
        return super().is_retryable(error)

    # ── Internals ────────────────────────────────────────────────────────

    def _headers(self, task: Optional[MelaTask]) -> dict[str, str]:
        headers = {"Content-Type": "application/json"}
        if self._api_key:
            headers[self._auth_header] = self._api_key
        if task is not None:
            # End-to-end correlation — workers can echo this back into
            # their own logs for cross-system tracing.
            headers["X-Mela-Trace-Id"] = task.trace_id
        return headers

    def _build_arguments(self, task: MelaTask) -> dict[str, Any]:
        """Build the MCP arguments dict.

        Caller-provided ``task.params`` win, but ``user_id`` and
        ``tenant_id`` are ALWAYS overlaid from the canonical
        ``task.context``.  This is a hard rule: many MCP workers fall
        back to "first user in DB" when these are omitted.  Never let
        them go missing or be overridden by stale params.
        """
        args: dict[str, Any] = dict(task.params or {})
        args["user_id"] = task.context.user_id
        args["tenant_id"] = task.context.tenant_id
        # Correlation keys so an async worker can echo the EXACT task_id /
        # trace_id back to /api/v1/ingest/result and resolve the pending
        # task in-place (resolved_pending=True) instead of landing as a
        # synthetic row.  Additive + ignored by workers that don't use them.
        args["mela_task_id"] = task.task_id
        args["trace_id"] = task.trace_id
        return args

    def _failure(
        self,
        task: MelaTask,
        *,
        code: str,
        message: str,
        retryable: bool,
        latency_ms: int,
    ) -> MelaResult:
        return MelaResult.failure(
            task=task,
            code=code,
            message=message,
            retryable=retryable,
            latency_ms=latency_ms,
            source=self.manifest.id,
        )

    def _success(
        self, task: MelaTask, payload: dict[str, Any], *, latency_ms: int
    ) -> MelaResult:
        # MCP servers commonly wrap output in {"content": [...]} or {"result": {...}}.
        data = payload if isinstance(payload, dict) else {"value": payload}
        summary = self._summarize(task.capability, data)
        return MelaResult(
            task_id=task.task_id,
            trace_id=task.trace_id,
            worker_id=self.manifest.id,
            capability=task.capability,
            success=True,
            data=data,
            summary=summary,
            metadata=MelaResultMetadata(
                latency_ms=latency_ms, source=self.manifest.id
            ),
        )

    @staticmethod
    def _summarize(capability: str, data: dict[str, Any]) -> str:
        # Best-effort one-liner.  Planner consumes this; full data goes to KB.
        if "tasks" in data and isinstance(data["tasks"], list):
            return f"{capability}: {len(data['tasks'])} tasks returned"
        if "scans" in data and isinstance(data["scans"], list):
            return f"{capability}: {len(data['scans'])} scan runs returned"
        if "result" in data and isinstance(data["result"], dict):
            keys = list(data["result"].keys())[:3]
            return f"{capability}: result keys={keys}"
        return f"{capability}: ok"


# Back-compat alias — anything that imported TaskRadarAdapter still works.
# Phase 1 used the worker-specific name; Phase 2 generalises it but keeps
# the alias so external consumers don't break.
TaskRadarAdapter = MCPAdapter
