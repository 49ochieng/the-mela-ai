"""Phase 3a (CR-3) — Code-level user-confirmation gate for dangerous tools.

The LLM is **never trusted** to bypass this gate. Confirmation tokens are
minted only by the explicit user-confirmation API endpoint, stored in
process memory keyed by ``(user_id, tool_name, arg_hash)``, single-use, and
expire after a short TTL.

Why an in-process store and not Redis? The tokens are short-lived (60 s)
and only valid for one tool dispatch on a single chat turn — they never
need to survive a process restart or a load-balancer hop within the SSE
session because the same process is serving the streaming response.
"""

from __future__ import annotations

import hashlib
import json
import secrets
import time
from dataclasses import dataclass
from threading import RLock
from typing import Any, Dict, Optional, Tuple


# Tools that REQUIRE an explicit, code-verified user confirmation token
# before they will dispatch. The LLM cannot bypass this set.
DANGEROUS_TOOLS: frozenset[str] = frozenset({
    "send_email",
    "schedule_meeting",
    "run_python_code",
})

# Token TTL — short on purpose. A confirmation token is intended to bridge
# a single user-click → tool-dispatch round-trip, not to be cached.
_TOKEN_TTL_SECONDS = 60

# Cap on outstanding tokens to bound memory.
_MAX_OUTSTANDING = 1024


@dataclass
class _TokenEntry:
    expires_at: float
    arg_hash: str
    tool_name: str
    user_id: str


_store: Dict[str, _TokenEntry] = {}
_lock = RLock()


def _now() -> float:
    return time.monotonic()


def _purge_expired_locked() -> None:
    """Caller must hold the lock."""
    now = _now()
    expired = [k for k, v in _store.items() if v.expires_at <= now]
    for k in expired:
        _store.pop(k, None)
    # Hard cap: drop oldest if over budget.
    if len(_store) > _MAX_OUTSTANDING:
        ordered = sorted(_store.items(), key=lambda kv: kv[1].expires_at)
        for k, _ in ordered[: len(_store) - _MAX_OUTSTANDING]:
            _store.pop(k, None)


def hash_args(arguments: Any) -> str:
    """Stable hash over tool arguments for token binding.

    The hash binds a token to the EXACT arguments the user approved — the
    LLM cannot mint a token for one payload and reuse it for a different
    payload (e.g. swap the recipient address after the user clicks
    confirm).
    """
    try:
        canon = json.dumps(arguments, sort_keys=True, default=str)
    except Exception:
        canon = repr(arguments)
    return hashlib.sha256(canon.encode("utf-8")).hexdigest()


def issue_token(*, user_id: str, tool_name: str, arguments: Any) -> str:
    """Mint a one-shot confirmation token for the given (user, tool, args).

    Returns an opaque url-safe token string.
    """
    token = secrets.token_urlsafe(32)
    entry = _TokenEntry(
        expires_at=_now() + _TOKEN_TTL_SECONDS,
        arg_hash=hash_args(arguments),
        tool_name=tool_name,
        user_id=str(user_id),
    )
    with _lock:
        _purge_expired_locked()
        _store[token] = entry
    return token


def consume_token(
    *,
    token: str,
    user_id: str,
    tool_name: str,
    arguments: Any,
) -> bool:
    """Validate and atomically consume a confirmation token.

    Returns True iff the token was valid for this exact (user, tool, args)
    triple AND not expired. The token is deleted regardless of return value
    to prevent replay.
    """
    if not token or not isinstance(token, str):
        return False
    with _lock:
        _purge_expired_locked()
        entry = _store.pop(token, None)
    if entry is None:
        return False
    if entry.expires_at <= _now():
        return False
    if entry.user_id != str(user_id):
        return False
    if entry.tool_name != tool_name:
        return False
    if entry.arg_hash != hash_args(arguments):
        return False
    return True


def make_confirmation_required_result(
    *,
    tool_name: str,
    arguments: Dict[str, Any],
    reason: str = "user_confirmation_required",
) -> Dict[str, Any]:
    """Standard tool result shape that tells the chat layer to surface a
    confirmation prompt to the end-user instead of executing the call.
    """
    return {
        "requires_confirmation": True,
        "tool": tool_name,
        "reason": reason,
        "preview": _redact_for_preview(arguments),
        "message": (
            f"This action ({tool_name}) requires explicit user approval "
            "before it can run. Please confirm via the UI prompt."
        ),
    }


def _redact_for_preview(args: Dict[str, Any]) -> Dict[str, Any]:
    """Lightweight redaction for UI preview — never include secrets."""
    if not isinstance(args, dict):
        return {}
    redact_keys = {"password", "secret", "token", "api_key", "apikey",
                   "authorization", "auth", "credentials"}
    out: Dict[str, Any] = {}
    for k, v in args.items():
        kl = str(k).lower()
        if any(p in kl for p in redact_keys):
            out[k] = "<redacted>"
        elif isinstance(v, str) and len(v) > 300:
            out[k] = v[:300] + "…"
        else:
            out[k] = v
    return out


# ── Test helpers ──────────────────────────────────────────────────────────────


def _reset_for_tests() -> None:
    """Wipe the token store. ONLY for use in tests."""
    with _lock:
        _store.clear()


def _outstanding_count() -> int:
    """For test assertions."""
    with _lock:
        return len(_store)
