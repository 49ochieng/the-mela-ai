"""Phase 5 — OpenTelemetry / Application Insights initialization.

Single entrypoint that turns on Azure Monitor's distributed tracing,
metrics, and logs.  No-op when ``APPLICATIONINSIGHTS_CONNECTION_STRING``
is empty so local dev stays quiet.

Call ``configure_telemetry(app)`` exactly once during application startup,
after FastAPI is constructed.  The instrumentation is auto-discovered by
``azure-monitor-opentelemetry`` for FastAPI, requests, httpx, and SQLAlchemy.
"""
from __future__ import annotations

import logging
import os
from typing import Any

from app.core.config import settings

logger = logging.getLogger(__name__)

_configured: bool = False


def configure_telemetry(app: Any | None = None) -> bool:
    """Wire Azure Monitor distributed tracing.  Returns True if configured."""
    global _configured

    if _configured:
        return True

    conn = settings.APPLICATIONINSIGHTS_CONNECTION_STRING
    if not conn:
        logger.info("APPLICATIONINSIGHTS_CONNECTION_STRING not set — telemetry disabled")
        return False

    # Hand the connection string to azure-monitor-opentelemetry via env var so
    # all instrumentations pick it up uniformly.
    os.environ.setdefault("APPLICATIONINSIGHTS_CONNECTION_STRING", conn)

    try:
        from azure.monitor.opentelemetry import configure_azure_monitor  # type: ignore
    except ImportError:
        logger.warning(
            "azure-monitor-opentelemetry not installed; install it to enable Application Insights"
        )
        return False

    try:
        configure_azure_monitor(
            connection_string=conn,
            disable_offline_storage=False,
            # Logger name 'app' covers our entire app.* tree.
            logger_name="app",
        )
    except Exception as e:
        logger.warning("Application Insights init failed (%s); continuing without telemetry", e)
        return False

    # Best-effort FastAPI instrumentation if the package is available.
    if app is not None:
        try:
            from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor  # type: ignore
            FastAPIInstrumentor.instrument_app(app)
        except Exception as e:
            logger.debug("FastAPI OpenTelemetry instrumentation skipped: %s", e)

    logger.info("Application Insights / OpenTelemetry configured")
    _configured = True
    return True


# ── Span helper ─────────────────────────────────────────────────────────────


class _NullSpan:
    """No-op span used when OpenTelemetry is not installed/configured."""

    def __enter__(self):
        return self

    def __exit__(self, *_exc):
        return False

    def set_attribute(self, *_args, **_kwargs) -> None:
        return None

    def set_status(self, *_args, **_kwargs) -> None:
        return None

    def record_exception(self, *_args, **_kwargs) -> None:
        return None


def start_span(name: str, **attributes: Any):
    """Phase 7 (P7.2): start an OTel span; degrades to a no-op safely.

    Use as ``with start_span("tool.send_email", tool=name) as span: ...``.
    All ``attributes`` are recorded as span attributes when the OTel SDK
    is available and a tracer provider is set; otherwise the call is a
    cheap no-op (handy for tests and dev where OT isn't configured).
    """
    try:
        from opentelemetry import trace  # type: ignore
    except Exception:  # pragma: no cover — OT not installed
        return _NullSpan()

    tracer = trace.get_tracer("mela")
    span_cm = tracer.start_as_current_span(name)
    span = span_cm.__enter__()
    for k, v in attributes.items():
        try:
            span.set_attribute(k, v)
        except Exception:
            pass

    class _Wrapper:
        def __enter__(self_inner):
            return span

        def __exit__(self_inner, exc_type, exc, tb):
            if exc is not None:
                try:
                    span.record_exception(exc)
                except Exception:
                    pass
            return span_cm.__exit__(exc_type, exc, tb)

    return _Wrapper()
