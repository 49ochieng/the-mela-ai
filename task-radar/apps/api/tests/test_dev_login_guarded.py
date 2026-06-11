"""Phase 1.4 — /api/auth/dev-login must 404 outside development."""
from __future__ import annotations

import pytest
from httpx import ASGITransport, AsyncClient

from app.config import clear_settings_cache
from app.main import create_app


@pytest.mark.asyncio
async def test_dev_login_disabled_in_production(monkeypatch):
    monkeypatch.setenv("APP_ENV", "production")
    monkeypatch.setenv("COOKIE_SECURE", "true")
    # Production boot guard requires real-looking secrets.
    monkeypatch.setenv("JWT_SECRET", "x" * 64)
    monkeypatch.setenv("SECRET_KEY", "y" * 64)
    monkeypatch.setenv("TOKEN_ENCRYPTION_KEY", "z" * 44)
    monkeypatch.setenv("CSRF_ENABLED", "true")
    monkeypatch.setenv("RATE_LIMIT_ENABLED", "true")
    clear_settings_cache()
    try:
        app = create_app()
        async with AsyncClient(transport=ASGITransport(app=app),
                               base_url="http://t", follow_redirects=False) as c:
            r = await c.get("/api/auth/dev-login")
        assert r.status_code == 404
    finally:
        clear_settings_cache()
