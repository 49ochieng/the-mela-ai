"""
Task Radar capability: create_followup_tasks.

Creates one or more Microsoft Planner tasks via the Microsoft Graph API
(app-only / client-credentials token), then POSTs a MelaResult-shaped
callback to Mela's ingestion API so the orchestration brain can wake any
awaiter, surface a worker event, write the Knowledge Base, and notify the
user.

Contract notes
--------------
* The callback body matches Mela's ``IngestResultRequest`` (the flat,
  forgiving mirror of ``MelaResult`` defined in
  ``backend/app/orchestration/store.py`` / ``types.py``):
  task_id, trace_id, capability, success, data, summary, latency_ms,
  error_code, error_message, error_retryable.
* Callback auth headers are ``X-Worker-Id`` + ``X-Worker-Api-Key`` —
  validated by ``require_worker_api_key`` against the worker manifest's
  ``auth_config["inbound_api_key"]`` (= TASK_RADAR_INBOUND_API_KEY).
* This function NEVER raises.  Any failure still produces a callback so
  Mela is never left waiting.
"""

from __future__ import annotations

import logging
import os
import time
from typing import Any, Optional

import httpx

logger = logging.getLogger("task-radar.create_followup_tasks")

GRAPH_BASE = "https://graph.microsoft.com/v1.0"
_TOKEN_SCOPE = "https://graph.microsoft.com/.default"


# ── Graph app-only token ───────────────────────────────────────────────────


async def get_graph_token() -> Optional[str]:
    """Acquire an app-only Graph token via client-credentials.

    Returns ``None`` (never raises) when credentials are missing or the
    token endpoint rejects the request — callers degrade gracefully.
    """
    tenant_id = (os.getenv("AZURE_TENANT_ID") or "").strip()
    client_id = (os.getenv("AZURE_CLIENT_ID") or "").strip()
    client_secret = (os.getenv("AZURE_CLIENT_SECRET") or "").strip()
    if not (tenant_id and client_id and client_secret):
        logger.warning("Azure app credentials incomplete — cannot get token")
        return None

    url = f"https://login.microsoftonline.com/{tenant_id}/oauth2/v2.0/token"
    data = {
        "client_id": client_id,
        "client_secret": client_secret,
        "scope": _TOKEN_SCOPE,
        "grant_type": "client_credentials",
    }
    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            resp = await client.post(url, data=data)
        if resp.status_code >= 400:
            logger.error("token endpoint %s: %s", resp.status_code, resp.text[:300])
            return None
        return resp.json().get("access_token")
    except Exception as exc:  # noqa: BLE001
        logger.error("token acquisition failed: %s", exc)
        return None


async def _resolve_assignee_oid(
    token: str, assignee_email: str
) -> Optional[str]:
    """Look up an Entra object id for a user email (for Planner assignment)."""
    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            resp = await client.get(
                f"{GRAPH_BASE}/users/{assignee_email}",
                headers={"Authorization": f"Bearer {token}"},
                params={"$select": "id"},
            )
        if resp.status_code == 200:
            return resp.json().get("id")
        logger.warning(
            "assignee lookup %s → %s", assignee_email, resp.status_code
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("assignee lookup failed for %s: %s", assignee_email, exc)
    return None


async def _create_one_task(
    token: str,
    *,
    plan_id: str,
    bucket_id: str,
    item: dict[str, Any],
) -> dict[str, Any]:
    """Create a single Planner task. Returns a {ok, ...} result dict."""
    title = (item.get("title") or "").strip()
    if not title:
        return {"ok": False, "title": title, "error": "missing title"}

    body: dict[str, Any] = {"planId": plan_id, "title": title[:255]}
    if bucket_id:
        body["bucketId"] = bucket_id

    due = item.get("due_date")
    if due:
        # Graph wants ISO-8601 UTC with a trailing Z.
        body["dueDateTime"] = due if due.endswith("Z") else f"{due}T17:00:00Z"

    assignee_email = item.get("assignee_email")
    if assignee_email:
        oid = await _resolve_assignee_oid(token, assignee_email)
        if oid:
            body["assignments"] = {
                oid: {
                    "@odata.type": "#microsoft.graph.plannerAssignment",
                    "orderHint": " !",
                }
            }

    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.post(
                f"{GRAPH_BASE}/planner/tasks",
                headers={
                    "Authorization": f"Bearer {token}",
                    "Content-Type": "application/json",
                },
                json=body,
            )
        if resp.status_code >= 400:
            logger.error(
                "create task %r → %s: %s", title, resp.status_code, resp.text[:300]
            )
            return {
                "ok": False,
                "title": title,
                "error": f"graph {resp.status_code}: {resp.text[:200]}",
            }
        created = resp.json()
        task_id = created.get("id", "")
        return {
            "ok": True,
            "title": title,
            "task_id": task_id,
            "plan_id": plan_id,
            "due_date": due,
            # Planner has no per-task webUrl in the create response; build the
            # canonical task deep-link best-effort.
            "web_url": (
                f"https://tasks.office.com/Home/Task/{task_id}" if task_id else ""
            ),
        }
    except Exception as exc:  # noqa: BLE001
        logger.error("create task %r raised: %s", title, exc)
        return {"ok": False, "title": title, "error": str(exc)[:200]}


# ── Mela callback ──────────────────────────────────────────────────────────


async def _send_callback(payload: dict[str, Any]) -> None:
    """POST the result back to Mela's ingestion API (best-effort)."""
    base = (os.getenv("MELA_INGESTION_BASE_URL") or "").strip().rstrip("/")
    inbound_key = (os.getenv("TASK_RADAR_INBOUND_API_KEY") or "").strip()
    if not base:
        logger.warning(
            "MELA_INGESTION_BASE_URL not set — cannot deliver callback for "
            "task=%s",
            payload.get("task_id"),
        )
        return
    if not inbound_key:
        logger.warning(
            "TASK_RADAR_INBOUND_API_KEY not set — callback will be rejected 401"
        )

    url = f"{base}/api/v1/ingest/result"
    headers = {
        "X-Worker-Id": "task-radar",
        "X-Worker-Api-Key": inbound_key,
        "Content-Type": "application/json",
    }
    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.post(url, json=payload, headers=headers)
        if resp.status_code >= 400:
            logger.error(
                "callback rejected %s: %s", resp.status_code, resp.text[:300]
            )
        else:
            logger.info(
                "callback delivered task=%s status=%s",
                payload.get("task_id"), resp.status_code,
            )
    except Exception as exc:  # noqa: BLE001
        logger.error("callback delivery failed: %s", exc)


# ── Entry point (background task) ───────────────────────────────────────────


async def run_create_followup_tasks(
    *,
    arguments: dict[str, Any],
    trace_id: str,
    mela_task_id: str,
    capability: str = "create_followup_tasks",
) -> None:
    """Create Planner tasks for every item, then report back to Mela.

    Always sends exactly one callback — completed / partial / failed —
    so the orchestration brain is never left awaiting.
    """
    started = time.monotonic()
    items = arguments.get("items") or []
    plan_id = (
        (arguments.get("plan_id") or "").strip()
        or (os.getenv("TASK_RADAR_PLANNER_PLAN_ID") or "").strip()
    )
    bucket_id = (arguments.get("bucket_id") or "").strip()

    def _elapsed() -> int:
        return int((time.monotonic() - started) * 1000)

    # ── Guard rails ────────────────────────────────────────────────────────
    if not isinstance(items, list) or not items:
        await _send_callback(
            {
                "task_id": mela_task_id,
                "trace_id": trace_id,
                "capability": capability,
                "success": False,
                "status": "failed",
                "data": {"created_tasks": [], "errors": ["no items provided"]},
                "summary": "create_followup_tasks: no task items were provided",
                "latency_ms": _elapsed(),
                "error_code": "NO_ITEMS",
                "error_message": "arguments.items was empty or not a list",
                "error_retryable": False,
            }
        )
        return

    if not plan_id:
        await _send_callback(
            {
                "task_id": mela_task_id,
                "trace_id": trace_id,
                "capability": capability,
                "success": False,
                "status": "failed",
                "data": {"created_tasks": [], "errors": ["no plan_id"]},
                "summary": (
                    "create_followup_tasks: no plan_id provided and "
                    "TASK_RADAR_PLANNER_PLAN_ID is not set"
                ),
                "latency_ms": _elapsed(),
                "error_code": "NO_PLAN_ID",
                "error_message": "plan_id missing and no default configured",
                "error_retryable": False,
            }
        )
        return

    token = await get_graph_token()
    if not token:
        await _send_callback(
            {
                "task_id": mela_task_id,
                "trace_id": trace_id,
                "capability": capability,
                "success": False,
                "status": "failed",
                "data": {"created_tasks": [], "errors": ["graph token unavailable"]},
                "summary": "create_followup_tasks: could not acquire a Graph token",
                "latency_ms": _elapsed(),
                "error_code": "GRAPH_AUTH_FAILED",
                "error_message": "client-credentials token acquisition failed",
                "error_retryable": True,
            }
        )
        return

    # ── Create each task ───────────────────────────────────────────────────
    created: list[dict[str, Any]] = []
    errors: list[str] = []
    for item in items:
        if not isinstance(item, dict):
            errors.append(f"skipped non-object item: {item!r}")
            continue
        result = await _create_one_task(
            token, plan_id=plan_id, bucket_id=bucket_id, item=item
        )
        if result.get("ok"):
            created.append(
                {
                    "task_id": result["task_id"],
                    "title": result["title"],
                    "web_url": result.get("web_url", ""),
                    "due_date": result.get("due_date"),
                }
            )
        else:
            errors.append(
                f"{result.get('title') or '(untitled)'}: {result.get('error')}"
            )

    # ── Build + send callback ──────────────────────────────────────────────
    total = len(items)
    n_ok = len(created)
    if n_ok == total and not errors:
        success, status = True, "completed"
        error_code = error_message = None
    elif n_ok > 0:
        success, status = True, "partial"
        error_code = "PARTIAL_FAILURE"
        error_message = "; ".join(errors)[:500]
    else:
        success, status = False, "failed"
        error_code = "ALL_TASKS_FAILED"
        error_message = "; ".join(errors)[:500]

    titles = ", ".join(t["title"] for t in created) or "(none)"
    summary = (
        f"Created {n_ok} of {total} Planner task(s) in plan {plan_id}: {titles}"
    )
    if errors:
        summary += f" — {len(errors)} failed"

    await _send_callback(
        {
            "task_id": mela_task_id,
            "trace_id": trace_id,
            "capability": capability,
            "success": success,
            "status": status,
            "data": {
                "plan_id": plan_id,
                "created_count": n_ok,
                "requested_count": total,
                "created_tasks": created,
                "errors": errors,
            },
            "summary": summary,
            "latency_ms": _elapsed(),
            "error_code": error_code,
            "error_message": error_message,
            "error_retryable": False,
        }
    )
