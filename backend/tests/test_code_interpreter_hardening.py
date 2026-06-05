"""Phase 4 (CR-1) — code-interpreter hardening tests.

Covers:
  * AST-level rejection of sandbox-escape patterns
  * AST allowance of safe everyday code (pandas, os.path.join)
  * Per-user concurrency gate (asyncio Semaphore + acquire timeout)
  * Per-user daily-quota gate (in-process counter fallback)
  * `open()` wrapper boilerplate blocks path traversal at runtime
"""
from __future__ import annotations

import asyncio
import importlib

import pytest

ci_mod = importlib.import_module(
    "app.services.code_interpreter_service"
)


# ─────────────────────────── AST validator ────────────────────────────────


@pytest.mark.parametrize(
    "src",
    [
        "__import__('os').system('echo x')",
        "import importlib",
        "import importlib\nimportlib.import_module('subprocess')",
        "import ctypes",
        "import subprocess",
        "from subprocess import Popen",
        "eval('1+1')",
        "exec('print(1)')",
        "compile('1', '<x>', 'exec')",
        "import os\nos.system('echo x')",
        "import os\nos.popen('echo x')",
        "().__class__.__mro__[-1].__subclasses__()",
        "getattr(object(), '__subclasses__')",
        "getattr(object(), 'system')",
        "breakpoint()",
    ],
)
def test_ast_blocks_sandbox_escapes(src):
    err = ci_mod._validate_ast(src)
    assert err is not None, f"expected reject for: {src!r}"
    assert "Disallowed" in err


@pytest.mark.parametrize(
    "src",
    [
        "import os\nprint(os.path.join('a', 'b'))",
        "import sys\nprint(sys.version_info)",
        "import json\nprint(json.dumps({'a': 1}))",
        "import pandas as pd\n# pd.read_csv would run; not executed here\n",
        "x = 1 + 2\nprint(x)",
        "for i in range(3):\n    print(i)",
    ],
)
def test_ast_allows_safe_code(src):
    assert ci_mod._validate_ast(src) is None, f"unexpected reject: {src!r}"


def test_validate_code_safety_uses_ast_first():
    """_validate_code_safety must invoke AST before regex layer."""
    msg = ci_mod.CodeInterpreterService()._validate_code_safety(
        "__import__('os').system('x')"
    )
    assert msg is not None
    assert "Disallowed" in msg


# ─────────────────────────── Concurrency gate ─────────────────────────────


@pytest.mark.asyncio
async def test_concurrency_gate_rejects_second_call(monkeypatch):
    """Two parallel runs for the same user → 2nd raises 429 quickly."""
    # Short acquire timeout so the test stays fast.
    monkeypatch.setattr(ci_mod, "_USER_ACQUIRE_TIMEOUT_S", 0.1)
    # Force a fresh semaphore registry to avoid test pollution.
    monkeypatch.setattr(
        ci_mod.CodeInterpreterService, "_user_semaphores", {}
    )

    svc = ci_mod.CodeInterpreterService()

    # Replace the actual subprocess execution with a slow stub so we can
    # observe the second caller hitting the semaphore.
    async def _slow_unguarded(code, timeout, input_files):
        await asyncio.sleep(0.5)
        return ci_mod.CodeResult(stdout="ok", stderr="", success=True)

    monkeypatch.setattr(svc, "_run_unguarded", _slow_unguarded)

    async def _call():
        return await svc.run("print(1)", user_id="user-concurrent")

    first = asyncio.create_task(_call())
    # Tiny yield so `first` grabs the semaphore before `second` tries.
    await asyncio.sleep(0.01)
    with pytest.raises(ci_mod.CodeInterpreterError) as exc_info:
        await svc.run("print(2)", user_id="user-concurrent")
    assert exc_info.value.status_code == 429
    assert "Another code job" in exc_info.value.message
    # Clean up.
    await first


@pytest.mark.asyncio
async def test_concurrency_gate_skipped_without_user_id(monkeypatch):
    """Legacy callers (user_id=None) bypass the semaphore."""
    monkeypatch.setattr(ci_mod, "_USER_ACQUIRE_TIMEOUT_S", 0.1)
    svc = ci_mod.CodeInterpreterService()

    async def _fast_unguarded(code, timeout, input_files):
        return ci_mod.CodeResult(stdout="ok", stderr="", success=True)

    monkeypatch.setattr(svc, "_run_unguarded", _fast_unguarded)
    res = await svc.run("print(1)")  # no user_id
    assert res.success is True


# ─────────────────────────── Daily quota gate ─────────────────────────────


@pytest.mark.asyncio
async def test_daily_quota_blocks_after_limit(monkeypatch):
    """In-process counter rejects (limit + 1)-th call as 429."""
    # Pin a tiny daily limit.
    monkeypatch.setattr(ci_mod, "_DAILY_LIMIT", 2)
    # Force a fresh quota bucket.
    monkeypatch.setattr(
        ci_mod.CodeInterpreterService,
        "_inproc_quota",
        ci_mod.defaultdict(dict),
    )
    # Force the Redis branch to be unavailable so the in-process fallback
    # is exercised deterministically.
    import app.core.redis_client as _rc

    async def _no_redis():
        return None

    monkeypatch.setattr(_rc, "get_redis", _no_redis)

    svc = ci_mod.CodeInterpreterService()

    async def _ok_unguarded(code, timeout, input_files):
        return ci_mod.CodeResult(stdout="ok", stderr="", success=True)

    monkeypatch.setattr(svc, "_run_unguarded", _ok_unguarded)

    # First two are allowed.
    await svc.run("print(1)", user_id="user-quota")
    await svc.run("print(2)", user_id="user-quota")
    # Third trips the gate.
    with pytest.raises(ci_mod.CodeInterpreterError) as exc_info:
        await svc.run("print(3)", user_id="user-quota")
    assert exc_info.value.status_code == 429
    assert "quota" in exc_info.value.message.lower()


# ─────────────────────────── open() boilerplate ───────────────────────────


@pytest.mark.asyncio
async def test_open_path_traversal_blocked_at_runtime():
    """End-to-end: user code that opens a path outside sandbox fails.

    Runs an actual subprocess — slower than the unit tests above. Skipped
    if the host Python lacks the imports the boilerplate needs.
    """
    svc = ci_mod.CodeInterpreterService()
    code = (
        "try:\n"
        "    open('../../../etc/hosts').read()\n"
        "    print('LEAKED')\n"
        "except PermissionError as e:\n"
        "    print('BLOCKED:', e)\n"
    )
    # No user_id → bypass concurrency/quota; we only assert sandbox open().
    res = await svc.run(code)
    combined = (res.stdout or "") + (res.stderr or "")
    assert "LEAKED" not in combined
    assert "Mela sandbox" in combined or "BLOCKED" in combined
