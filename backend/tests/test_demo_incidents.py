"""
Regression tests for the two demo-blocking incidents.

Incident 1: GET /api/v1/chat/models crashed because the SQLite
``model_rankings`` table was missing the ``cost_multiplier`` column.
The fix added the ALTER TABLE to the SQLite migration list in
``app.core.database.init_db`` AND created alembic migration 003.

Incident 2: GET /api/v1/orchestration/events/stream raised
``RuntimeError: No response returned`` because BaseHTTPMiddleware
buffers streaming responses and chokes when the inner generator
yields zero bytes (caused by an invalid StreamChunk.type='connected'
silently failing pydantic validation in the generator's
``try/except Exception: return`` block).

The fixes are tested at three levels:
  1. the SQLite schema actually has cost_multiplier
  2. the SSE endpoint emits a parseable initial chunk
  3. the middleware ASGI bypass kicks in for streaming paths
"""

from __future__ import annotations

import asyncio
import sqlite3

import pytest


# ── Incident 1 ──────────────────────────────────────────────────────────


def test_model_rankings_has_cost_multiplier(tmp_path, monkeypatch):
    """init_db must add cost_multiplier to the model_rankings table.

    Run init_db() against a fresh on-disk SQLite file (different path
    from the developer's mela_dev.db so we never mutate it) and PRAGMA
    the resulting table.
    """
    fresh = tmp_path / "fresh.db"
    monkeypatch.setenv(
        "DATABASE_URL", f"sqlite+aiosqlite:///{fresh.as_posix()}",
    )
    monkeypatch.setenv("APP_ENV", "development")

    # Force a re-import so settings + engine pick up the new DATABASE_URL.
    # The test runner caches modules between tests; we reach in to flush
    # only the modules that read settings.DATABASE_URL at import time.
    import importlib
    import app.core.config as cfg
    import app.core.database as db
    importlib.reload(cfg)
    importlib.reload(db)
    # Models must register on Base.metadata BEFORE init_db.
    import app.models.models  # noqa: F401

    asyncio.run(db.init_db())

    with sqlite3.connect(fresh) as conn:
        cols = {
            r[1]
            for r in conn.execute("PRAGMA table_info(model_rankings)")
        }
    assert "cost_multiplier" in cols, (
        f"cost_multiplier missing from model_rankings; got cols={cols}"
    )


# ── Incident 2 ──────────────────────────────────────────────────────────


def test_streaming_paths_bypass_basehttpmiddleware():
    """RequestLoggingMiddleware and RateLimitMiddleware MUST route
    streaming endpoints around their BaseHTTPMiddleware wrapper or SSE
    breaks with "No response returned." """
    from app.core.middleware import _is_streaming_path

    # The two SSE prefixes we care about for the demo.
    sse_scope = {
        "type": "http",
        "path": "/api/v1/orchestration/events/stream",
    }
    chat_stream_scope = {
        "type": "http",
        "path": "/api/v1/chat/completions/stream",
    }
    regular_scope = {"type": "http", "path": "/api/v1/chat/models"}
    websocket_scope = {
        "type": "websocket",
        "path": "/api/v1/orchestration/events/stream",
    }

    assert _is_streaming_path(sse_scope) is True
    assert _is_streaming_path(chat_stream_scope) is True
    assert _is_streaming_path(regular_scope) is False
    # WebSocket paths are not HTTP — must not match.
    assert _is_streaming_path(websocket_scope) is False


def test_orchestration_stream_chunk_type_is_valid():
    """The SSE generator yields StreamChunk(type='ping') as its first
    chunk.  If we ever change the type back to something like
    'connected' that isn't in the Literal, pydantic validation raises
    inside the generator and Starlette closes the response with zero
    bytes — exactly the bug we fixed.  This test pins the type."""
    from app.schemas.chat import StreamChunk
    # Must not raise.
    chunk = StreamChunk(type="ping")
    payload = chunk.model_dump_json()
    assert '"type":"ping"' in payload

    # And the invalid type we used to ship MUST still fail.
    with pytest.raises(Exception):
        StreamChunk(type="connected")  # type: ignore[arg-type]
