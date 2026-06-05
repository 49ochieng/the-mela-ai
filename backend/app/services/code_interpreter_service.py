"""
Mela AI - Code Interpreter Service

Executes Python code safely in a subprocess, captures output,
and collects any files written to the working directory.
Supports generating Word, Excel, PDF, CSV, charts (PNG), JSON, and more.
"""

import ast
import asyncio
import base64
import logging
import mimetypes
import os
import re
import sys
import tempfile
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

# ── Safety limits ──────────────────────────────────────────────────────────────
_TIMEOUT_SECONDS = 60               # raised from 30 — complex doc operations need headroom
_MAX_OUTPUT_BYTES = 100_000         # 100 KB stdout/stderr cap
_MAX_FILE_SIZE = 10 * 1024 * 1024  # 10 MB per generated file

# Phase 4 (CR-1): per-user concurrency + daily-quota guards.
_USER_CONCURRENCY = int(os.environ.get("CODE_INTERPRETER_USER_CONCURRENCY", "1"))
_USER_ACQUIRE_TIMEOUT_S = float(os.environ.get(
    "CODE_INTERPRETER_ACQUIRE_TIMEOUT_S", "10"
))
_DAILY_LIMIT = int(os.environ.get("CODE_INTERPRETER_DAILY_LIMIT", "50"))


class CodeInterpreterError(Exception):
    """Raised by the gate when execution is refused (quota / concurrency)."""

    def __init__(self, message: str, *, status_code: int = 429):
        super().__init__(message)
        self.message = message
        self.status_code = status_code


# Only allow these extensions to be returned to the user
_ALLOWED_EXTENSIONS = {
    ".txt", ".csv", ".json", ".xml", ".html", ".md",
    ".pdf", ".docx", ".xlsx", ".pptx",
    ".png", ".jpg", ".jpeg", ".svg", ".gif",
    ".zip",                              # multi-file project bundles
    ".py", ".js", ".ts", ".sql", ".r",  # generated code files
}

# Credential env-var prefixes to strip from the sandbox environment.
# This prevents user code from reading Azure keys, DB connection strings, etc.
_SANDBOX_ENV_BLOCKLIST_PREFIXES = (
    "AZURE_", "DATABASE_", "OPENAI_", "JWT_", "SECRET_",
    "MSAL_", "DEV_PASSWORD", "DEV_USERNAME", "AI_FOUNDRY",
    "APPLICATIONINSIGHTS_", "WEBSITE_AUTH_",
)

_SANDBOX_ENV_ALLOWED_KEYS = {
    "PATH",
    "SYSTEMROOT",
    "WINDIR",
    "COMSPEC",
    "PATHEXT",
    "TMP",
    "TEMP",
    "HOME",
    "USERPROFILE",
    "NUMBER_OF_PROCESSORS",
    "LANG",
    "LC_ALL",
    "TZ",
}

_SENSITIVE_ENV_PATTERNS = (
    "KEY", "TOKEN", "SECRET", "PASSWORD", "CONNECTION_STRING", "CONNSTR",
)

_DISALLOWED_CODE_PATTERNS = (
    r"(^|\s)import\s+subprocess(\s|$)",
    r"(^|\s)from\s+subprocess\s+import(\s|$)",
    r"(^|\s)import\s+socket(\s|$)",
    r"(^|\s)from\s+socket\s+import(\s|$)",
    r"(^|\s)import\s+requests(\s|$)",
    r"(^|\s)from\s+requests\s+import(\s|$)",
    r"(^|\s)import\s+urllib(\s|$)",
    r"(^|\s)from\s+urllib\s+import(\s|$)",
    r"(^|\s)import\s+ftplib(\s|$)",
    r"(^|\s)from\s+ftplib\s+import(\s|$)",
    # Phase 4 (CR-1): additions to close documented bypasses.
    r"(^|\s)import\s+ctypes(\s|$)",
    r"(^|\s)from\s+ctypes\s+import(\s|$)",
    r"(^|\s)import\s+importlib(\s|$)",
    r"(^|\s)from\s+importlib\s+import(\s|$)",
    r"(^|\s)import\s+multiprocessing(\s|$)",
    r"(^|\s)from\s+multiprocessing\s+import(\s|$)",
    r"(^|\s)import\s+pty(\s|$)",
    r"(^|\s)from\s+pty\s+import(\s|$)",
)

# ── Phase 4 (CR-1): AST-based validator ──────────────────────────────────────
# Regex catches obvious `import X` lines but is trivially bypassed by
# `__import__('os')`, `importlib.import_module('subprocess')`, or
# `eval(compile('...', '<x>', 'exec'))`. The AST validator parses the user
# code and walks every node to catch those patterns at the syntax level.

_BANNED_MODULES = frozenset({
    "subprocess", "socket", "requests", "urllib", "urllib2", "urllib3",
    "httpx", "aiohttp",
    "ftplib", "ctypes", "importlib", "multiprocessing", "pty", "select",
    "resource", "signal",
})

_BANNED_CALLEES = frozenset({
    "__import__", "eval", "exec", "compile",
    # `breakpoint()` launches pdb, which can spawn arbitrary subprocesses.
    "breakpoint",
})

# Attribute-chain endings we never allow regardless of how the leftmost
# name resolved (catches things like `__builtins__.eval`, `os.system`,
# `getattr(x, "system")` constructed via attribute access).
_BANNED_ATTR_NAMES = frozenset({
    "system", "popen", "spawn", "spawnl", "spawnv", "spawnve", "spawnvp",
    "spawnvpe", "execv", "execvp", "execve", "execvpe", "execle", "execlp",
    "execlpe", "execl",
    "fork", "forkpty", "kill",
    "__import__",
})

# Names whose mere mention in user code is suspicious — typically used in
# sandbox-escape chains via `().__class__.__mro__[-1].__subclasses__()`.
_BANNED_DUNDER_NAMES = frozenset({
    "__subclasses__", "__bases__", "__mro__", "__globals__", "__getitem__",
    "__class__",  # used to walk to `object` then to subprocess.Popen
    "__builtins__",
})


def _validate_ast(source: str) -> Optional[str]:
    """Walk ``source`` as Python AST; return error message or ``None``.

    Detects documented sandbox-bypass patterns that the regex layer misses:
      * ``__import__('os').system(...)``
      * ``importlib.import_module('subprocess')``
      * ``eval('...')`` / ``exec('...')`` / ``compile(...)``
      * Dunder-walk escapes via ``().__class__.__mro__``
      * Direct attribute access to ``os.system`` / ``os.popen`` / etc.
    """
    try:
        tree = ast.parse(source)
    except SyntaxError as exc:
        # Defer to subprocess to produce a real syntax error message —
        # user-friendly traceback shown in the chat result. We only fail
        # CLOSED for security violations.
        return None if isinstance(exc, SyntaxError) else f"AST parse error: {exc}"

    for node in ast.walk(tree):
        # 1. import banned_module
        if isinstance(node, ast.Import):
            for alias in node.names:
                root = (alias.name or "").split(".")[0]
                if root in _BANNED_MODULES:
                    return f"Disallowed import: {alias.name!r}"

        # 2. from banned_module import X
        elif isinstance(node, ast.ImportFrom):
            root = (node.module or "").split(".")[0]
            if root in _BANNED_MODULES:
                return f"Disallowed import: {node.module!r}"

        # 3. Direct calls to banned builtins: __import__, eval, exec, compile
        elif isinstance(node, ast.Call):
            func = node.func
            if isinstance(func, ast.Name) and func.id in _BANNED_CALLEES:
                return f"Disallowed function call: {func.id!r}"
            # 3b. getattr(obj, 'banned_name') — defeats attribute scan
            if (isinstance(func, ast.Name) and func.id == "getattr"
                    and node.args and isinstance(node.args[-1], ast.Constant)
                    and isinstance(node.args[-1].value, str)
                    and (node.args[-1].value in _BANNED_ATTR_NAMES
                         or node.args[-1].value in _BANNED_DUNDER_NAMES)):
                return (
                    "Disallowed getattr() target: "
                    f"{node.args[-1].value!r}"
                )

        # 4. Attribute access ending in banned name (os.system, ...popen, ...)
        elif isinstance(node, ast.Attribute):
            if node.attr in _BANNED_ATTR_NAMES:
                return f"Disallowed attribute access: {node.attr!r}"
            if node.attr in _BANNED_DUNDER_NAMES:
                return f"Disallowed dunder access: {node.attr!r}"

        # 5. Bare name reference to dangerous dunders (e.g. __builtins__)
        elif isinstance(node, ast.Name):
            if node.id in _BANNED_DUNDER_NAMES:
                return f"Disallowed name reference: {node.id!r}"

    return None


# Pre-import boilerplate prepended to every execution.
# Wrapped in try/except so missing packages never cause execution failures.
#
# IMPORTANT: security patches (subprocess/socket blocking) MUST come AFTER all
# library imports. On Windows, fpdf2 → sign.py → unittest.mock → asyncio →
# windows_utils tries to subclass subprocess.Popen. If subprocess.Popen has
# already been replaced with our blocker function, Python raises:
#   TypeError: function() argument 'code' must be code, not str
# So: import everything first, then patch.
_PREIMPORT_BOILERPLATE = """\
import os as _os

# ── 1. Pre-load all libraries (BEFORE any security patches) ───────────────
try:
    import pandas as pd
    import numpy as np
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    import seaborn as sns
    sns.set_theme(style='whitegrid')
except ImportError:
    pass
try:
    import openpyxl
    import xlsxwriter
except ImportError:
    pass
try:
    import docx
except ImportError:
    pass
try:
    import fitz  # PyMuPDF
except ImportError:
    pass

# ── Unicode-safe PDF helpers ──────────────────────────────────────────────
# fpdf2 core fonts (Helvetica/Arial/Times) are latin-1 only.
# Use safe_text() to sanitise any string before passing it to cell()/text().
# Use PDF.para() for body text — it auto-wraps with multi_cell.
try:
    from fpdf import FPDF as _FPDF

    _UNICODE_MAP = {
        '\\u2022': '-', '\\u2023': '-', '\\u2043': '-',  # bullets
        '\\u2019': "'", '\\u2018': "'",                   # smart quotes
        '\\u201c': '"', '\\u201d': '"',                   # smart dbl-quotes
        '\\u2013': '-', '\\u2014': '-',                   # en/em dash
        '\\u2026': '...', '\\u00a0': ' ',                 # ellipsis, NBSP
        '\\u2192': '->', '\\u2190': '<-', '\\u2713': '+', # arrows, checkmark
        '\\u00b7': '-', '\\u00b0': 'deg',                 # middle dot, degree
        '\\u00e9': 'e', '\\u00e8': 'e', '\\u00e0': 'a',  # accents
        '\\u00f3': 'o', '\\u00fa': 'u', '\\u00ed': 'i',
    }

    def safe_text(s):
        \"\"\"Replace common Unicode chars so fpdf2 core fonts render them.\"\"\"
        if not s:
            return ''
        for ch, rep in _UNICODE_MAP.items():
            s = s.replace(ch, rep)
        return s.encode('latin-1', errors='replace').decode('latin-1')

    class PDF(_FPDF):
        \"\"\"FPDF with Unicode-safe helpers and sensible defaults.

        Usage:
            pdf = PDF()
            pdf.add_page()
            pdf.heading('Title', size=16)
            pdf.para('Body text that wraps automatically.')
            pdf.save('report.pdf')
        \"\"\"
        def __init__(self, orientation='P', unit='mm', format='A4'):
            super().__init__(orientation=orientation, unit=unit,
                             format=format)
            self.set_auto_page_break(auto=True, margin=15)
            self.set_margins(15, 15, 15)

        def heading(self, text, size=14, bold=True):
            style = 'B' if bold else ''
            self.set_font('Helvetica', style, size)
            self.multi_cell(0, size * 0.5, safe_text(text))
            self.ln(2)

        def para(self, text, size=11, line_height=6):
            self.set_font('Helvetica', '', size)
            self.multi_cell(0, line_height, safe_text(text))
            self.ln(2)

        def bullet(self, text, size=11):
            self.set_font('Helvetica', '', size)
            self.multi_cell(0, 6, '- ' + safe_text(text))

        def save(self, filename):
            self.output(filename)

    FPDF = PDF  # alias so old FPDF() usage picks up helpers automatically
except ImportError:
    pass

# ── 2. Security rails — applied AFTER all imports ────────────────────────
# Patching subprocess.Popen BEFORE imports breaks asyncio.windows_utils on
# Windows (it tries to subclass subprocess.Popen, which would now be a fn).
def _mela_blocked(*_a, **_k):
    raise PermissionError("Operation blocked in Mela code sandbox")

try:
    import subprocess as _mela_subprocess
    _mela_subprocess.Popen = _mela_blocked
    _mela_subprocess.call = _mela_blocked
    _mela_subprocess.run = _mela_blocked
except Exception:
    pass

try:
    import socket as _mela_socket
    _mela_socket.create_connection = _mela_blocked
    _mela_socket.getaddrinfo = _mela_blocked
except Exception:
    pass

_os.system = _mela_blocked
_os.popen = _mela_blocked

# ── Phase 4 (CR-1): confine open() to the sandbox cwd ─────────────────────
# User code is expected to read/write files in the working directory only.
# Reject absolute paths and any path containing '..' segments so a stray
# `open('/etc/passwd')` cannot succeed even if a syscall slips through.
import builtins as _mela_builtins
import os.path as _mela_ospath
_mela_real_open = _mela_builtins.open
_mela_sandbox_root = _mela_ospath.realpath(_os.getcwd())


def _mela_safe_open(file, mode='r', *args, **kwargs):
    # File-descriptor opens (int) are allowed (e.g. stdin/stdout) — no path.
    if isinstance(file, int):
        return _mela_real_open(file, mode, *args, **kwargs)
    p = _os.fspath(file)
    if not isinstance(p, str):
        raise PermissionError("Mela sandbox: open() path must be a string")
    if '\\x00' in p:
        raise PermissionError("Mela sandbox: null byte in path")
    # Reject obvious traversal segments before resolution.
    norm = p.replace('\\\\', '/').strip()
    if '/..' in '/' + norm or norm.startswith('..'):
        raise PermissionError(
            f"Mela sandbox: parent-directory traversal blocked ({p!r})"
        )
    # Resolve relative paths against cwd; absolute paths must already be
    # inside the sandbox root.
    resolved = _mela_ospath.realpath(
        p if _mela_ospath.isabs(p) else _mela_ospath.join(_os.getcwd(), p)
    )
    root_with_sep = _mela_sandbox_root.rstrip('/\\\\') + _os.sep
    if not (resolved == _mela_sandbox_root or resolved.startswith(root_with_sep)):
        raise PermissionError(
            f"Mela sandbox: open() outside sandbox ({p!r} → {resolved!r})"
        )
    return _mela_real_open(resolved, mode, *args, **kwargs)


_mela_builtins.open = _mela_safe_open
"""

# Code wrapper: changes CWD to the temp work dir before running user code
_CODE_WRAPPER = """
import sys as _sys, os as _os, traceback as _tb
_os.chdir('{work_dir}')
try:
{indented_code}
except SystemExit:
    pass
except Exception as _exc:
    print(f"Error: {{_exc}}", file=_sys.stderr)
    _tb.print_exc(file=_sys.stderr)
"""


@dataclass
class GeneratedFile:
    name: str
    base64_data: str
    mime_type: str
    size: int


@dataclass
class CodeResult:
    stdout: str = ""
    stderr: str = ""
    success: bool = True
    files: List[GeneratedFile] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "stdout": self.stdout,
            "stderr": self.stderr,
            "success": self.success,
            "files": [
                {
                    "name": f.name,
                    "base64": f.base64_data,
                    "mime_type": f.mime_type,
                    "size": f.size,
                }
                for f in self.files
            ],
        }


class CodeInterpreterService:
    """Execute Python code safely inside a subprocess."""

    # Phase 4 (CR-1): per-user concurrency guards + in-process quota
    # fallback when Redis is unavailable.
    _user_semaphores: Dict[str, asyncio.Semaphore] = {}
    _user_semaphore_lock = asyncio.Lock()
    _inproc_quota: Dict[str, Dict[str, int]] = defaultdict(dict)

    async def _get_user_semaphore(self, user_id: str) -> asyncio.Semaphore:
        """Lazily mint a Semaphore per user (singleton per process)."""
        async with self._user_semaphore_lock:
            sem = self._user_semaphores.get(user_id)
            if sem is None:
                sem = asyncio.Semaphore(_USER_CONCURRENCY)
                self._user_semaphores[user_id] = sem
            return sem

    async def _check_and_increment_quota(self, user_id: str) -> None:
        """Increment today's execution counter; raise if user is over budget.

        Uses Redis when available so the quota is enforced across replicas;
        falls back to a per-process dict otherwise.
        """
        today = datetime.now(timezone.utc).strftime("%Y%m%d")

        # Try Redis first.
        try:
            from app.core.redis_client import get_redis, key as rkey
            r = await get_redis()
            if r is not None:
                k = rkey("quota", "code_exec", user_id, today)
                # INCR is atomic; set 25h expiry on first increment.
                count = await r.incr(k)  # type: ignore[union-attr]
                if int(count) == 1:
                    await r.expire(k, 25 * 3600)  # type: ignore[union-attr]
                if int(count) > _DAILY_LIMIT:
                    raise CodeInterpreterError(
                        f"Daily code-execution quota exceeded "
                        f"({_DAILY_LIMIT}/day). Try again tomorrow.",
                        status_code=429,
                    )
                return
        except CodeInterpreterError:
            raise
        except Exception as exc:
            logger.debug(
                "Redis quota check failed (%s); using in-process counter", exc
            )

        # In-process fallback.
        bucket = self._inproc_quota[today]
        bucket[user_id] = bucket.get(user_id, 0) + 1
        if bucket[user_id] > _DAILY_LIMIT:
            raise CodeInterpreterError(
                f"Daily code-execution quota exceeded "
                f"({_DAILY_LIMIT}/day). Try again tomorrow.",
                status_code=429,
            )

    def _validate_code_safety(self, code: str) -> Optional[str]:
        # Phase 4 (CR-1): AST first — catches __import__/eval/getattr tricks.
        ast_err = _validate_ast(code)
        if ast_err:
            return ast_err
        for pat in _DISALLOWED_CODE_PATTERNS:
            if re.search(pat, code, flags=re.IGNORECASE | re.MULTILINE):
                return "Disallowed module usage detected (network/process execution)."
        return None

    def _build_sandbox_env(self) -> Dict[str, str]:
        env: Dict[str, str] = {}
        for key in _SANDBOX_ENV_ALLOWED_KEYS:
            val = os.environ.get(key)
            if val:
                env[key] = val

        for key, val in os.environ.items():
            if key.startswith("PYTHON"):
                env[key] = val

        # Defensive scrub for any sensitive key names that slipped through.
        for key in list(env.keys()):
            up = key.upper()
            if any(up.startswith(p) for p in _SANDBOX_ENV_BLOCKLIST_PREFIXES):
                env.pop(key, None)
                continue
            if any(s in up for s in _SENSITIVE_ENV_PATTERNS):
                env.pop(key, None)

        env.update({
            "MPLBACKEND": "Agg",
            "PYTHONDONTWRITEBYTECODE": "1",
            "PYTHONIOENCODING": "utf-8",
            "PYTHONNOUSERSITE": "1",
        })
        return env

    async def run(
        self,
        code: str,
        timeout: int = _TIMEOUT_SECONDS,
        input_files: Optional[List[Dict[str, str]]] = None,
        user_id: Optional[str] = None,
    ) -> CodeResult:
        """Run *code* and return stdout, stderr, and any generated files.

        Args:
            code: Python source to execute.
            timeout: Max seconds to allow.
            input_files: Optional list of {"name": filename, "base64": b64_str}
                         dicts. Each file is written to the work directory before
                         the code runs so the code can open them by name.
            user_id: Caller identity for per-user concurrency + quota gates.
                     When ``None`` (legacy callers / internal helpers) the
                     gates are skipped — the LLM-facing tool dispatcher MUST
                     pass it.
        """
        safety_error = self._validate_code_safety(code)
        if safety_error:
            return CodeResult(stdout="", stderr=safety_error, success=False)

        # Phase 4 (CR-1): concurrency + quota gates. Skipped for None
        # user_id (back-compat for tests + internal callers).
        if user_id:
            await self._check_and_increment_quota(user_id)
            sem = await self._get_user_semaphore(user_id)
            try:
                await asyncio.wait_for(
                    sem.acquire(), timeout=_USER_ACQUIRE_TIMEOUT_S,
                )
            except asyncio.TimeoutError:
                raise CodeInterpreterError(
                    "Another code job is still running for your account. "
                    "Please wait for it to complete.",
                    status_code=429,
                )
            try:
                return await self._run_unguarded(code, timeout, input_files)
            finally:
                sem.release()

        return await self._run_unguarded(code, timeout, input_files)

    async def _run_unguarded(
        self,
        code: str,
        timeout: int,
        input_files: Optional[List[Dict[str, str]]],
    ) -> CodeResult:
        # Sprint 4.2: when USE_GVISOR_RUNTIME is on and CODE_RUNNER_URL is set,
        # dispatch to the gVisor sidecar (Azure Container App). The sandbox AST
        # validator already ran on this process — defence in depth.
        from app.core.config import settings as _settings
        if (
            getattr(_settings, "USE_GVISOR_RUNTIME", False)
            and getattr(_settings, "CODE_RUNNER_URL", "")
        ):
            try:
                return await self._dispatch_to_sidecar(
                    code, timeout, input_files
                )
            except Exception as exc:
                logger.error(
                    "gVisor sidecar dispatch failed (%s); falling back to local",
                    exc,
                )

        with tempfile.TemporaryDirectory(prefix="mela_code_") as work_dir:
            # Pre-write any caller-supplied input files
            if input_files:
                for f in input_files:
                    name = f.get("name", "")
                    b64 = f.get("base64", "")
                    if name and b64:
                        try:
                            dest = Path(work_dir) / Path(name).name
                            dest.write_bytes(base64.b64decode(b64))
                        except Exception as exc:
                            logger.warning(f"Could not write input file {name!r}: {exc}")
            return await self._execute(code, work_dir, timeout)

    async def _dispatch_to_sidecar(
        self,
        code: str,
        timeout: int,
        input_files: Optional[List[Dict[str, str]]],
    ) -> CodeResult:
        """POST the job to the gVisor sidecar.

        The sidecar is a separate Azure Container App running with the
        ``runsc`` (gVisor) runtime; it accepts ``{code, timeout, input_files}``
        and returns ``{stdout, stderr, success, files: [...]}`` with the
        same shape as ``CodeResult.to_dict()``.
        """
        import httpx
        from app.core.config import settings as _settings

        url = _settings.CODE_RUNNER_URL.rstrip("/") + "/execute"
        headers = {"Content-Type": "application/json"}
        api_key = getattr(_settings, "CODE_RUNNER_API_KEY", "")
        if api_key:
            headers["X-Api-Key"] = api_key

        payload = {
            "code": code,
            "timeout": timeout,
            "input_files": input_files or [],
        }
        async with httpx.AsyncClient(timeout=timeout + 30) as client:
            resp = await client.post(url, json=payload, headers=headers)
            resp.raise_for_status()
            data = resp.json()

        files: List[GeneratedFile] = []
        for f in (data.get("files") or []):
            try:
                files.append(GeneratedFile(
                    name=f.get("name", ""),
                    base64_data=f.get("base64_data", ""),
                    mime_type=f.get("mime_type", "application/octet-stream"),
                    size=int(f.get("size", 0)),
                ))
            except Exception as _f_err:
                logger.debug("Skipping malformed sidecar file: %s", _f_err)

        return CodeResult(
            stdout=data.get("stdout", ""),
            stderr=data.get("stderr", ""),
            success=bool(data.get("success", False)),
            files=files,
        )

    async def _execute(self, code: str, work_dir: str, timeout: int) -> CodeResult:
        # Prepend boilerplate then indent everything for the wrapper
        augmented = _PREIMPORT_BOILERPLATE + "\n" + code
        indented = "\n".join(f"    {line}" for line in augmented.splitlines())

        # Use forward slashes for cross-platform compatibility - Python accepts them on Windows
        safe_work_dir = work_dir.replace("\\", "/")
        wrapper = _CODE_WRAPPER.format(
            work_dir=safe_work_dir,
            indented_code=indented or "    pass",
        )

        env = self._build_sandbox_env()

        # Run the subprocess synchronously inside a thread executor.
        # This avoids the Windows uvicorn issue where the default
        # WindowsSelectorEventLoopPolicy raises NotImplementedError from
        # asyncio.create_subprocess_exec(), and works identically on POSIX.
        import subprocess

        def _run_sync() -> tuple[int, bytes, bytes]:
            proc = subprocess.Popen(  # noqa: S603 - controlled args
                [sys.executable, "-I", "-B", "-c", wrapper],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                stdin=subprocess.DEVNULL,
                cwd=work_dir,
                env=env,
            )
            try:
                out, err = proc.communicate(timeout=timeout)
                return proc.returncode, out, err
            except subprocess.TimeoutExpired:
                try:
                    proc.kill()
                    out, err = proc.communicate()
                except Exception:
                    out, err = b"", b""
                raise
            except Exception:
                try:
                    proc.kill()
                except Exception:
                    pass
                raise

        loop = asyncio.get_event_loop()
        try:
            try:
                returncode, raw_out, raw_err = await loop.run_in_executor(
                    None, _run_sync
                )
            except subprocess.TimeoutExpired:
                return CodeResult(
                    stdout="",
                    stderr=(
                        f"⏱ Code execution timed out after {timeout} seconds."
                    ),
                    success=False,
                )

            stdout = raw_out[:_MAX_OUTPUT_BYTES].decode("utf-8", errors="replace")
            stderr = raw_err[:_MAX_OUTPUT_BYTES].decode("utf-8", errors="replace")
            success = returncode == 0
            files = self._collect_files(work_dir)
            logger.debug(
                "CI: returncode=%s out_len=%d err_len=%d files=%d",
                returncode, len(raw_out), len(raw_err), len(files),
            )
            return CodeResult(
                stdout=stdout,
                stderr=stderr,
                success=success,
                files=files,
            )

        except Exception as exc:
            logger.error(
                "Code interpreter internal error: %s: %s",
                type(exc).__name__, exc, exc_info=True,
            )
            return CodeResult(
                stdout="",
                stderr=f"{type(exc).__name__}: {exc}",
                success=False,
            )

    def _collect_files(self, work_dir: str) -> List[GeneratedFile]:
        """Collect files written by the user code (up to size limit)."""
        collected: List[GeneratedFile] = []
        work_path = Path(work_dir)
        for fp in sorted(work_path.iterdir()):
            if not fp.is_file():
                continue
            if fp.suffix.lower() not in _ALLOWED_EXTENSIONS:
                continue
            size = fp.stat().st_size
            if size > _MAX_FILE_SIZE:
                logger.warning(f"Generated file {fp.name!r} exceeds size limit — skipped")
                continue
            try:
                data = fp.read_bytes()
                b64 = base64.b64encode(data).decode("utf-8")
                mime = mimetypes.guess_type(fp.name)[0] or "application/octet-stream"
                collected.append(GeneratedFile(
                    name=fp.name,
                    base64_data=b64,
                    mime_type=mime,
                    size=size,
                ))
            except Exception as exc:
                logger.warning(f"Could not read generated file {fp.name!r}: {exc}")
        return collected


# ── Singleton ──────────────────────────────────────────────────────────────────
code_interpreter = CodeInterpreterService()
