"""
Mela AI - Worker discovery probe.

Used by the admin "Connect a worker" UI to talk to a candidate worker
URL + key BEFORE persisting anything in the registry.  Calls the
worker's MCP ``tools/list`` shape (industry default) and its health
endpoint, returns a suggested ``WorkerManifest`` skeleton plus the
discovered capability list.

This module is intentionally side-effect free — it never writes to
the registry, the breaker store, or any DB row.  Persistence happens
only when the admin clicks Save and the UI follows up with
``PUT /registry/{id}``.

Hard rules
----------

* Never raises.  Every failure path returns ``ProbeResult.failure(...)``
  with one of the public error codes.
* Tight 8-second timeout for both calls — slow probes degrade UX.
* ``/probe`` only knows MCP-shaped workers.  REST workers go straight
  to the manual manifest form on the frontend.
"""

from __future__ import annotations

import logging
import re
import time
from dataclasses import dataclass
from typing import Any, Optional

import httpx

logger = logging.getLogger(__name__)


# ── Error codes (stable contract with the frontend) ─────────────────────

PROBE_TIMEOUT = "PROBE_TIMEOUT"
PROBE_AUTH_FAILED = "PROBE_AUTH_FAILED"
PROBE_UNREACHABLE = "PROBE_UNREACHABLE"
PROBE_BAD_SHAPE = "PROBE_BAD_SHAPE"
PROBE_NO_TOOLS = "PROBE_NO_TOOLS"
PROBE_INTERNAL = "PROBE_INTERNAL"


# ── Public dataclasses ───────────────────────────────────────────────────


@dataclass
class DiscoveredCapability:
    name: str
    description: str = ""
    input_params: dict[str, Any] = None  # type: ignore[assignment]
    is_async: bool = False


@dataclass
class ProbeResult:
    success: bool
    base_url: str
    suggested_id: Optional[str] = None
    suggested_display_name: Optional[str] = None
    suggested_version: Optional[str] = None
    capabilities: list[DiscoveredCapability] = None  # type: ignore[assignment]
    health_ok: Optional[bool] = None
    health_latency_ms: Optional[int] = None
    error_code: Optional[str] = None
    error_message: Optional[str] = None

    @classmethod
    def failure(cls, base_url: str, code: str, message: str) -> "ProbeResult":
        return cls(
            success=False,
            base_url=base_url,
            error_code=code,
            error_message=message,
            capabilities=[],
        )


# ── Probe ────────────────────────────────────────────────────────────────


_PROBE_TIMEOUT_S = 8.0
_SLUG_RE = re.compile(r"[^a-z0-9_-]+")


def _slugify(value: str) -> str:
    """Best-effort id slug from a worker's display name or hostname."""
    base = (value or "").strip().lower().replace(" ", "_")
    base = _SLUG_RE.sub("", base)
    return base[:64] or "worker"


def _id_from_url(url: str) -> str:
    try:
        host = httpx.URL(url).host or ""
    except Exception:
        host = ""
    # First DNS label, e.g. taskradar.example.com -> taskradar
    label = host.split(".")[0] if host else "worker"
    return _slugify(label)


async def discover(
    *,
    base_url: str,
    api_key: Optional[str] = None,
    auth_header: str = "X-Api-Key",
    health_path: str = "/health",
) -> ProbeResult:
    """Probe a candidate MCP worker.

    Returns a ``ProbeResult`` — never raises.  ``success=True`` only if
    we got a usable ``tools/list`` response; health probe failure is
    reported but does not by itself flip success to False (the worker
    might not implement the conventional ``/health`` URL).
    """
    if not base_url or not isinstance(base_url, str):
        return ProbeResult.failure(
            base_url or "", PROBE_BAD_SHAPE, "base_url is required"
        )

    headers = {"Content-Type": "application/json"}
    if api_key:
        headers[auth_header or "X-Api-Key"] = api_key

    # 1. tools/list
    body = {"tool": "tools/list", "arguments": {}}
    try:
        async with httpx.AsyncClient(timeout=_PROBE_TIMEOUT_S) as client:
            resp = await client.post(base_url, json=body, headers=headers)
    except httpx.TimeoutException:
        return ProbeResult.failure(
            base_url, PROBE_TIMEOUT, f"no response within {_PROBE_TIMEOUT_S:.0f}s"
        )
    except (httpx.ConnectError, httpx.RemoteProtocolError) as exc:
        return ProbeResult.failure(
            base_url, PROBE_UNREACHABLE, f"{type(exc).__name__}: {exc}"
        )
    except Exception as exc:  # noqa: BLE001
        logger.exception("probe: tools/list crashed")
        return ProbeResult.failure(
            base_url, PROBE_INTERNAL, f"{type(exc).__name__}: {exc}"
        )

    if resp.status_code in (401, 403):
        return ProbeResult.failure(
            base_url, PROBE_AUTH_FAILED,
            f"worker rejected the api key (HTTP {resp.status_code})",
        )
    if resp.status_code >= 400:
        return ProbeResult.failure(
            base_url, PROBE_BAD_SHAPE,
            f"tools/list returned HTTP {resp.status_code}: {resp.text[:200]}",
        )

    try:
        payload = resp.json()
    except ValueError:
        return ProbeResult.failure(
            base_url, PROBE_BAD_SHAPE, "tools/list returned non-JSON body"
        )

    capabilities = _parse_tools(payload)
    if not capabilities:
        return ProbeResult.failure(
            base_url, PROBE_NO_TOOLS,
            "tools/list returned no tools — worker may not be MCP-shaped",
        )

    # 2. health (best-effort)
    health_ok: Optional[bool] = None
    health_latency_ms: Optional[int] = None
    health_url = _join_health_url(base_url, health_path)
    if health_url:
        started = time.monotonic()
        try:
            async with httpx.AsyncClient(timeout=_PROBE_TIMEOUT_S) as client:
                hresp = await client.get(health_url, headers=headers)
            health_latency_ms = int((time.monotonic() - started) * 1000)
            health_ok = hresp.status_code < 400
        except Exception:  # noqa: BLE001 — health is informational only
            health_ok = False
            health_latency_ms = int((time.monotonic() - started) * 1000)

    suggested_name = _suggested_display_name(payload, base_url)
    suggested_id = _slugify(suggested_name) if suggested_name else _id_from_url(base_url)
    suggested_version = str(payload.get("version") or "0.1.0")

    return ProbeResult(
        success=True,
        base_url=base_url,
        suggested_id=suggested_id,
        suggested_display_name=suggested_name or suggested_id.replace("_", " ").title(),
        suggested_version=suggested_version,
        capabilities=capabilities,
        health_ok=health_ok,
        health_latency_ms=health_latency_ms,
    )


# ── Helpers ──────────────────────────────────────────────────────────────


def _parse_tools(payload: Any) -> list[DiscoveredCapability]:
    """Accept both {"tools": [...]} and {"result": {"tools": [...]}} shapes."""
    if not isinstance(payload, dict):
        return []
    raw = payload.get("tools")
    if raw is None and isinstance(payload.get("result"), dict):
        raw = payload["result"].get("tools")
    if not isinstance(raw, list):
        return []

    out: list[DiscoveredCapability] = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        name = item.get("name")
        if not name or not isinstance(name, str):
            continue
        schema = item.get("inputSchema") or item.get("input_schema") or {}
        if not isinstance(schema, dict):
            schema = {}
        out.append(
            DiscoveredCapability(
                name=name,
                description=str(item.get("description") or ""),
                input_params=schema,
                is_async=bool(item.get("is_async") or item.get("isAsync")),
            )
        )
    return out


def _suggested_display_name(payload: Any, base_url: str) -> str:
    if isinstance(payload, dict):
        for key in ("display_name", "displayName", "name", "title"):
            value = payload.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip()
    return ""


def _join_health_url(base_url: str, health_path: str) -> str:
    if not health_path:
        return ""
    if health_path.startswith(("http://", "https://")):
        return health_path
    try:
        parsed = httpx.URL(base_url)
        return str(
            httpx.URL(
                scheme=parsed.scheme,
                host=parsed.host,
                port=parsed.port,
                path=health_path if health_path.startswith("/") else f"/{health_path}",
            )
        )
    except Exception:
        return ""
