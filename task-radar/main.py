"""
Mela Task Radar — independent MCP-over-HTTP worker.

A standalone FastAPI service (separate process from the Mela backend) that
Mela's orchestration brain dispatches tasks to over the wire.  It speaks the
same MCP-over-HTTP shape Mela's MCPAdapter expects:

    POST /
    Headers: X-Api-Key: <TASK_RADAR_MCP_API_KEY>
             X-Mela-Trace-Id: <trace_id>            (set by Mela's adapter)
    Body:    {"tool": "<capability>", "arguments": {...}}

For async capabilities the worker returns ``{"status": "accepted", ...}``
immediately and does the real work in a background task, POSTing a
MelaResult-shaped callback to Mela's ingestion API when finished.

The cardinal rule (see ORCHESTRATION.md): this worker NEVER depends on Mela
to run.  If Mela's ingestion endpoint is down the callback simply fails and
is logged; the worker keeps serving.
"""

from __future__ import annotations

import asyncio
import logging
import os
import uuid
from typing import Any

import httpx
from dotenv import load_dotenv
from fastapi import FastAPI, Header, HTTPException, Request
from fastapi.responses import JSONResponse

from capabilities.create_followup_tasks import (
    get_graph_token,
    run_create_followup_tasks,
)

load_dotenv()

logging.basicConfig(
    level=os.getenv("LOG_LEVEL", "INFO"),
    format="%(asctime)s %(levelname)s [task-radar] %(message)s",
)
logger = logging.getLogger("task-radar")

VERSION = "1.0.0"
WORKER_ID = "task-radar"

# Capabilities that run asynchronously: the dispatcher returns immediately
# and the result lands later via Mela's /api/v1/ingest/result callback.
_ASYNC_TOOLS = {"create_followup_tasks"}

app = FastAPI(title="Mela Task Radar", version=VERSION)


def _require_api_key(x_api_key: str | None) -> None:
    """Validate the inbound MCP key Mela presents on every call."""
    expected = (os.getenv("TASK_RADAR_MCP_API_KEY") or "").strip()
    if not expected:
        # Fail closed: refuse to run unauthenticated in any environment.
        logger.error("TASK_RADAR_MCP_API_KEY not configured — rejecting call")
        raise HTTPException(status_code=503, detail="worker not configured")
    if not x_api_key or x_api_key.strip() != expected:
        raise HTTPException(status_code=401, detail="invalid X-Api-Key")


# ── MCP dispatcher ─────────────────────────────────────────────────────────


@app.post("/")
async def dispatch(
    request: Request,
    x_api_key: str | None = Header(default=None, alias="X-Api-Key"),
    x_mela_trace_id: str | None = Header(default=None, alias="X-Mela-Trace-Id"),
) -> JSONResponse:
    """Single MCP dispatcher keyed on the ``tool`` body field."""
    _require_api_key(x_api_key)

    try:
        body = await request.json()
    except Exception:
        raise HTTPException(status_code=400, detail="body must be JSON")

    tool = (body or {}).get("tool")
    arguments = (body or {}).get("arguments") or {}
    if not tool or not isinstance(arguments, dict):
        raise HTTPException(
            status_code=400,
            detail="body must be {tool: str, arguments: object}",
        )

    # Mela's adapter overlays user_id / tenant_id into arguments and carries
    # the trace id in the X-Mela-Trace-Id header.  The orchestration task id
    # to report back against may arrive either as an explicit
    # ``mela_task_id`` argument or be derived from the trace id.
    trace_id = (
        arguments.get("trace_id")
        or x_mela_trace_id
        or str(uuid.uuid4())
    )
    mela_task_id = (
        arguments.get("mela_task_id")
        or arguments.get("task_id")
        or trace_id
    )

    logger.info(
        "dispatch tool=%s trace=%s task=%s user=%s tenant=%s",
        tool, trace_id, mela_task_id,
        arguments.get("user_id"), arguments.get("tenant_id"),
    )

    if tool in _ASYNC_TOOLS:
        if tool == "create_followup_tasks":
            # Fire-and-forget: do the work in the background, return now.
            asyncio.create_task(
                run_create_followup_tasks(
                    arguments=arguments,
                    trace_id=trace_id,
                    mela_task_id=mela_task_id,
                    capability=tool,
                )
            )
        return JSONResponse(
            {
                "status": "accepted",
                "task_id": mela_task_id,
                "trace_id": trace_id,
                "tool": tool,
            }
        )

    raise HTTPException(status_code=404, detail=f"unknown tool: {tool!r}")


# ── Health ───────────────────────────────────────────────────────────────


@app.get("/health")
async def health(deep: bool = False) -> dict[str, Any]:
    """Liveness + optional deep Graph-connectivity probe.

    Mela's seed stamps the health URL as ``base_url + /health?deep=true``,
    so a deep probe is what the orchestration health summary actually hits.
    """
    out: dict[str, Any] = {
        "status": "ok",
        "worker": WORKER_ID,
        "version": VERSION,
    }
    if deep:
        try:
            token = await get_graph_token()
            out["graph"] = "ok" if token else "unreachable"
            if not token:
                out["status"] = "degraded"
        except Exception as exc:  # noqa: BLE001 — health probe must not raise
            out["graph"] = f"error: {type(exc).__name__}"
            out["status"] = "degraded"
    return out


if __name__ == "__main__":
    import uvicorn

    port = int(os.getenv("PORT", "8001"))
    uvicorn.run(app, host="0.0.0.0", port=port)
