"""
demo_readiness_check.py
-----------------------

Single-shot smoke check before a Mela AI demo.

Verifies:
  - backend /health (local OR remote)
  - DB connectivity (via /health.checks.db)
  - Azure OpenAI / Search wiring (via /health.checks)
  - /api/v1/chat/models returns models (Incident 1 regression check)
  - /api/v1/settings/models returns rankings WITH cost_multiplier
  - /api/v1/orchestration/events/stream opens an SSE channel without
    crashing with "No response returned" (Incident 2 regression check)

Each check prints "PASS" or "FAIL: <reason>" and the script exits non-zero
if any FAIL was emitted, so it works in CI / pre-demo automation too.

Usage:
  python scripts/demo_readiness_check.py [--base-url URL] [--token TOKEN]

If --token is omitted, attempts /api/v1/auth/dev-login with dev/dev
(requires ENABLE_DEV_LOGIN=true on the target backend).
"""

from __future__ import annotations

import argparse
import json
import sys
from typing import Optional

import urllib.request
import urllib.error
import socket


DEFAULT_BASE_URL = "http://127.0.0.1:8765"


class Report:
    def __init__(self) -> None:
        self.results: list[tuple[str, bool, str]] = []

    def add(self, name: str, ok: bool, note: str = "") -> None:
        self.results.append((name, ok, note))
        status = "PASS" if ok else "FAIL"
        prefix = "\033[92m" if ok else "\033[91m"
        reset = "\033[0m"
        suffix = f" — {note}" if note else ""
        print(f"  [{prefix}{status}{reset}] {name}{suffix}")

    @property
    def all_passed(self) -> bool:
        return all(r[1] for r in self.results)


def http_get(
    url: str,
    token: Optional[str] = None,
    timeout: float = 30.0,
) -> tuple[int, bytes, dict]:
    req = urllib.request.Request(url)
    if token:
        req.add_header("Authorization", f"Bearer {token}")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.status, resp.read(), dict(resp.headers)
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read() or b"", dict(exc.headers or {})


def http_post(
    url: str, body: dict, timeout: float = 15.0,
) -> tuple[int, bytes]:
    data = json.dumps(body).encode("utf-8")
    req = urllib.request.Request(
        url, data=data, headers={"Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.status, resp.read()
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read() or b""


def get_dev_token(base_url: str) -> Optional[str]:
    try:
        status, body = http_post(
            f"{base_url}/api/v1/auth/dev-login",
            {"username": "dev", "password": "dev"},
        )
    except Exception as exc:
        print(f"  (dev-login error: {exc})")
        return None
    if status != 200:
        return None
    try:
        return json.loads(body.decode())["access_token"]
    except Exception:
        return None


def check_health(base_url: str, report: Report) -> None:
    print("\n[1] Backend health")
    try:
        status, body, _ = http_get(f"{base_url}/health", timeout=15)
    except Exception as exc:
        report.add("health endpoint reachable", False, str(exc)[:80])
        return
    if status != 200:
        report.add("health endpoint reachable", False, f"HTTP {status}")
        return
    try:
        j = json.loads(body.decode())
    except Exception as exc:
        report.add("health JSON parse", False, str(exc)[:60])
        return
    report.add("health endpoint reachable", True, f"env={j.get('environment')}")
    checks = j.get("checks", {}) or {}
    report.add("db connectivity", checks.get("db") == "ok", str(checks.get("db")))
    openai = checks.get("openai")
    report.add(
        "openai configured",
        openai in ("configured", "ok"),
        str(openai),
    )
    report.add(
        "search configured",
        bool(checks.get("search_configured")),
        str(checks.get("search_configured")),
    )


def check_models(base_url: str, token: str, report: Report) -> None:
    print("\n[2] /api/v1/chat/models  (Incident 1 regression)")
    try:
        status, body, _ = http_get(
            f"{base_url}/api/v1/chat/models", token=token, timeout=60,
        )
    except Exception as exc:
        report.add("/chat/models reachable", False, str(exc)[:80])
        return
    if status != 200:
        report.add("/chat/models reachable", False, f"HTTP {status}")
        return
    try:
        models = json.loads(body.decode())
    except Exception:
        report.add("/chat/models JSON parse", False, "non-JSON body")
        return
    report.add("/chat/models returns 200", True, f"{len(models)} models")
    report.add("/chat/models has at least 3 models", len(models) >= 3,
               f"{len(models)} returned")


def check_settings_models(base_url: str, token: str, report: Report) -> None:
    print("\n[3] /api/v1/settings/models  (cost_multiplier field)")
    try:
        status, body, _ = http_get(
            f"{base_url}/api/v1/settings/models", token=token, timeout=30,
        )
    except Exception as exc:
        report.add("/settings/models reachable", False, str(exc)[:80])
        return
    if status != 200:
        report.add("/settings/models reachable", False, f"HTTP {status}")
        return
    rows = json.loads(body.decode())
    report.add("/settings/models 200", True, f"{len(rows)} rows")
    has_cost = all("cost_multiplier" in r for r in rows) if rows else False
    report.add(
        "every row has cost_multiplier",
        has_cost,
        "found" if has_cost else "MISSING",
    )
    if rows:
        first = rows[0]
        report.add(
            f"first row valid (model_id={first.get('model_id')}, mult={first.get('cost_multiplier')})",
            isinstance(first.get("cost_multiplier"), (int, float)),
        )


def check_sse(base_url: str, token: str, report: Report) -> None:
    """Open SSE for ~5s and confirm at least one byte arrives before
    EOF.  Crashes here previously raised RuntimeError("No response
    returned.") inside middleware."""
    print("\n[4] /api/v1/orchestration/events/stream  (Incident 2 regression)")
    url = f"{base_url}/api/v1/orchestration/events/stream"
    req = urllib.request.Request(url)
    req.add_header("Authorization", f"Bearer {token}")
    try:
        resp = urllib.request.urlopen(req, timeout=25)
    except urllib.error.HTTPError as exc:
        report.add("SSE 200 OK", False, f"HTTP {exc.code}")
        return
    except Exception as exc:
        report.add("SSE 200 OK", False, str(exc)[:80])
        return
    report.add("SSE 200 OK", resp.status == 200, str(resp.status))
    ct = resp.headers.get("content-type", "")
    report.add("SSE Content-Type", "text/event-stream" in ct, ct)
    # Read first SSE line (terminated by \n).  Using read(N) blocks until
    # the buffer fills — the first chunk is only ~90 bytes followed by
    # 30s of silence, so a fixed-size read deadlocks.
    try:
        sock = resp.fp.raw._sock if hasattr(resp.fp, "raw") else None
        if sock is not None:
            sock.settimeout(8)
    except Exception:
        pass
    try:
        first_line = resp.readline(512)
    except socket.timeout:
        report.add("SSE first chunk arrived", False, "timeout")
        return
    except Exception as exc:
        report.add("SSE first chunk arrived", False, str(exc)[:60])
        return
    finally:
        try:
            resp.close()
        except Exception:
            pass
    report.add(
        "SSE first chunk arrived",
        len(first_line) > 0 and first_line.startswith(b"data:"),
        f"{len(first_line)} bytes",
    )


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--base-url", default=DEFAULT_BASE_URL)
    ap.add_argument("--token", default=None,
                    help="Bearer token; if omitted, tries /auth/dev-login")
    args = ap.parse_args()

    base = args.base_url.rstrip("/")
    print(f"Mela AI demo readiness check — target: {base}")

    report = Report()
    check_health(base, report)

    token = args.token or get_dev_token(base)
    if not token:
        print("\nNo auth token available — skipping authenticated checks.")
        print("Pass --token <jwt> to run the full suite against a deploy "
              "with ENABLE_DEV_LOGIN=false.")
    else:
        check_models(base, token, report)
        check_settings_models(base, token, report)
        check_sse(base, token, report)

    total = len(report.results)
    passed = sum(1 for r in report.results if r[1])
    print(f"\n=== {passed}/{total} checks passed ===")
    if not report.all_passed:
        print("Demo NOT ready — fix the failures above.")
        return 1
    print("Demo ready.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
