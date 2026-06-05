"""
Mela AI - Logging Configuration
"""

import logging
import re
import sys
from typing import Optional

from app.core.config import settings


# ── Secret-redaction filter ──────────────────────────────────────────────────
# Matches common patterns that should never appear verbatim in logs:
#   - JWT-shaped tokens (xxx.yyy.zzz where each part is base64url)
#   - Bearer tokens
#   - key/secret/password/token=<value> in URLs and query strings
#   - Authorization header values
_REDACT_PATTERNS = [
    re.compile(r"eyJ[A-Za-z0-9_\-]{10,}\.[A-Za-z0-9_\-]{10,}\.[A-Za-z0-9_\-]{10,}"),
    re.compile(r"(?i)bearer\s+[A-Za-z0-9_\-\.=]+"),
    re.compile(r"(?i)(api[-_]?key|secret|password|token|client[-_]?secret)"
               r"[\"']?\s*[:=]\s*[\"']?[A-Za-z0-9_\-\.=+/]{6,}"),
    re.compile(r"(?i)authorization[\"']?\s*:\s*[\"']?[^\s\"',}]+"),
]


class _SecretRedactFilter(logging.Filter):
    """Logging filter that redacts secret-shaped substrings from log records."""

    def _scrub(self, value: str) -> str:
        scrubbed = value
        for pat in _REDACT_PATTERNS:
            scrubbed = pat.sub("[REDACTED]", scrubbed)
        return scrubbed

    def filter(self, record: logging.LogRecord) -> bool:
        try:
            if isinstance(record.msg, str):
                record.msg = self._scrub(record.msg)
            if record.args:
                if isinstance(record.args, dict):
                    record.args = {
                        k: self._scrub(v) if isinstance(v, str) else v
                        for k, v in record.args.items()
                    }
                else:
                    record.args = tuple(
                        self._scrub(a) if isinstance(a, str) else a
                        for a in record.args
                    )
        except Exception:
            # Logging filters MUST NOT raise; fail-open so we don't drop the log.
            pass
        return True


def setup_logging(level: Optional[str] = None) -> None:
    """Setup application logging."""
    log_level = getattr(logging, level or settings.LOG_LEVEL.upper(), logging.INFO)

    # Create formatter
    formatter = logging.Formatter(
        fmt="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    # Console handler
    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setLevel(log_level)
    console_handler.setFormatter(formatter)
    console_handler.addFilter(_SecretRedactFilter())

    # Root logger configuration
    root_logger = logging.getLogger()
    root_logger.setLevel(log_level)
    root_logger.addHandler(console_handler)
    # Attach the redact filter at the root too so loggers that bypass our
    # console handler (e.g. App Insights) still get scrubbed records.
    root_logger.addFilter(_SecretRedactFilter())

    # Azure Application Insights handler
    if settings.APPLICATIONINSIGHTS_CONNECTION_STRING:
        try:
            from opencensus.ext.azure.log_exporter import AzureLogHandler
            azure_handler = AzureLogHandler(
                connection_string=settings.APPLICATIONINSIGHTS_CONNECTION_STRING
            )
            azure_handler.setLevel(log_level)
            azure_handler.setFormatter(formatter)
            root_logger.addHandler(azure_handler)
            logging.info("Azure Application Insights logging enabled")
        except ImportError:
            logging.warning("opencensus-ext-azure not installed, skipping App Insights logging")
        except Exception as e:
            logging.warning(f"Failed to setup Azure logging: {e}")

    # Suppress noisy loggers
    logging.getLogger("azure").setLevel(logging.WARNING)
    logging.getLogger("urllib3").setLevel(logging.WARNING)
    logging.getLogger("httpx").setLevel(logging.WARNING)

    logging.info(f"Logging configured at level: {settings.LOG_LEVEL}")


class AuditLogger:
    """Audit logger for tracking user actions."""

    def __init__(self):
        self.logger = logging.getLogger("audit")

    def log_action(
        self,
        user_id: str,
        action: str,
        resource: str,
        details: Optional[dict] = None,
        success: bool = True,
    ) -> None:
        """Log an auditable action."""
        log_data = {
            "user_id": user_id,
            "action": action,
            "resource": resource,
            "success": success,
            "details": details or {},
        }

        if success:
            self.logger.info(f"AUDIT: {log_data}")
        else:
            self.logger.warning(f"AUDIT_FAILED: {log_data}")


audit_logger = AuditLogger()


# ── Phase 0: central security-event helper ───────────────────────────────────
# Single entrypoint for emitting AuditLog rows from any request handler.
# Caller owns the transaction (commit/rollback) — this helper only adds + flushes.

def extract_audit_context(request) -> dict:
    """Pull ip_address and user_agent out of a FastAPI/Starlette request.

    Safe to call with ``None`` or any object missing the expected attributes —
    returns an empty-ish dict in that case so callers never need to guard.
    """
    if request is None:
        return {"ip_address": None, "user_agent": None}
    try:
        client = getattr(request, "client", None)
        ip = getattr(client, "host", None) if client else None
        # Honour standard reverse-proxy headers when present.
        headers = getattr(request, "headers", {}) or {}
        fwd = headers.get("x-forwarded-for") if hasattr(headers, "get") else None
        if fwd:
            ip = fwd.split(",")[0].strip()
        ua = headers.get("user-agent") if hasattr(headers, "get") else None
        return {"ip_address": ip, "user_agent": ua}
    except Exception:
        return {"ip_address": None, "user_agent": None}


async def log_security_event(
    db,
    *,
    user_id: str,
    action: str,
    event_type: Optional[str] = None,
    resource_type: str = "system",
    resource_id: Optional[str] = None,
    workspace_id: Optional[str] = None,
    details: Optional[dict] = None,
    ip_address: Optional[str] = None,
    user_agent: Optional[str] = None,
    success: bool = True,
    error_message: Optional[str] = None,
    request=None,
) -> "object":
    """Create and persist a single AuditLog row.

    The caller is responsible for ``await db.commit()`` — this helper only
    issues ``db.add`` + ``db.flush`` so it composes naturally inside an
    existing request transaction. ``request`` (optional) is used to backfill
    ``ip_address``/``user_agent`` when those are not explicitly passed.

    Returns the created ``AuditLog`` instance (with ``id`` populated by flush)
    or ``None`` if persistence failed (failures are swallowed — audit logging
    must never break the request).
    """
    # Imported lazily to avoid an import cycle with models <-> core.
    from app.models.models import AuditLog as _AuditLog

    if request is not None and (ip_address is None or user_agent is None):
        ctx = extract_audit_context(request)
        ip_address = ip_address or ctx["ip_address"]
        user_agent = user_agent or ctx["user_agent"]

    try:
        row = _AuditLog(
            user_id=user_id,
            action=action,
            event_type=event_type,
            resource_type=resource_type,
            resource_id=resource_id,
            workspace_id=workspace_id,
            details=details or {},
            ip_address=ip_address,
            user_agent=(user_agent or "")[:500] if user_agent else None,
            success=success,
            error_message=error_message,
        )
        db.add(row)
        await db.flush()
        return row
    except Exception as exc:
        # Never let audit logging break the caller. Log to the audit logger
        # so we still have a breadcrumb in the application logs.
        audit_logger.logger.warning(
            "log_security_event failed (user_id=%s action=%s): %s",
            user_id, action, exc,
        )
        return None

