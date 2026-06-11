"""Structured logging setup.

Production deployments emit JSON log lines so they can be ingested
losslessly by Azure Monitor / Log Analytics. Each line carries the
request id (set by RequestContextMiddleware) and any extra ``audit``
payload attached by the audit subsystem, enabling end-to-end trace
correlation. Sensitive header / token names are redacted.
"""
from __future__ import annotations

import json
import logging
import re
import sys
from datetime import datetime, timezone

from .config import get_settings
from .middleware.request_context import current as _current_request_ctx

# Names of fields that may carry secret material. We redact case-insensitively.
_REDACT_KEYS = (
    "access_token", "refresh_token", "id_token", "client_secret",
    "authorization", "token_reference", "refresh_token_reference",
    "azure_client_secret", "x-csrf-token", "cookie", "set-cookie",
    "jwt_secret", "secret_key", "token_encryption_key",
)

# Substring patterns that indicate raw secret material regardless of context.
_TOKEN_PATTERNS: tuple[re.Pattern[str], ...] = (
    # Bearer / agent tokens / OAuth access tokens
    re.compile(r"(?i)\bBearer\s+[A-Za-z0-9._\-]{8,}"),
    re.compile(r"\bmtr_at_[A-Za-z0-9_\-]{8,}"),
    re.compile(r"\beyJ[A-Za-z0-9._\-]{20,}"),  # JWT
    # Fernet token reference scheme
    re.compile(r"\bf1:[A-Za-z0-9_\-=]{20,}"),
    # Generic key=value secret leaks
    re.compile(
        r"(?i)(" + "|".join(re.escape(k) for k in _REDACT_KEYS) + r")"
        r"\s*[:=]\s*[^\s,;\"'}]{4,}"
    ),
)


def _redact(value: str) -> str:
    if not value:
        return value
    out = value
    for pat in _TOKEN_PATTERNS:
        out = pat.sub(lambda m: _mask(m.group(0)), out)
    return out


def _mask(token: str) -> str:
    # Preserve the leading label (e.g. `Authorization:`) so an operator
    # can still see *which* field was redacted, but mask the value tail.
    if ":" in token or "=" in token:
        sep = ":" if ":" in token else "="
        head, _, _ = token.partition(sep)
        return f"{head}{sep} ***"
    if token.lower().startswith("bearer "):
        return "Bearer ***"
    return "***"


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        ctx = _current_request_ctx()
        payload = {
            "ts": datetime.now(timezone.utc).isoformat(timespec="milliseconds"),
            "level": record.levelname,
            "logger": record.name,
            "msg": _redact(record.getMessage()),
        }
        if ctx is not None:
            payload["request_id"] = ctx.request_id
        if record.exc_info:
            payload["exc"] = self.formatException(record.exc_info)
        # Pass through structured `extra` fields the producer attached.
        for k, v in record.__dict__.items():
            if k in payload or k.startswith("_"):
                continue
            if k in {"args", "asctime", "created", "exc_info", "exc_text", "filename",
                     "funcName", "levelname", "levelno", "lineno", "message", "module",
                     "msecs", "msg", "name", "pathname", "process", "processName",
                     "relativeCreated", "stack_info", "thread", "threadName",
                     "taskName"}:
                continue
            try:
                json.dumps(v)
                payload[k] = v
            except Exception:
                payload[k] = repr(v)
        return json.dumps(payload, separators=(",", ":"), default=str)


class RedactingFormatter(logging.Formatter):
    """Plain-text formatter for local dev with the same redaction surface."""

    def format(self, record: logging.LogRecord) -> str:
        msg = _redact(super().format(record))
        return msg


def setup_logging() -> None:
    settings = get_settings()
    if hasattr(sys.stdout, "reconfigure"):
        try:
            sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass
    handler = logging.StreamHandler(sys.stdout)
    if settings.app_env == "production" or settings.log_format == "json":
        handler.setFormatter(JsonFormatter())
    else:
        handler.setFormatter(RedactingFormatter("%(asctime)s %(levelname)s %(name)s | %(message)s"))
    root = logging.getLogger()
    root.handlers = [handler]
    root.setLevel(settings.log_level.upper())


logger = logging.getLogger("taskradar")
