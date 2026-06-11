"""Phase 4: CSRF / rate-limit / security headers."""
from __future__ import annotations

import importlib

import pytest
from httpx import ASGITransport, AsyncClient

from app.config import clear_settings_cache, get_settings
from app.middleware.security_headers import header_names


def _reload_app(monkeypatch, **env: str):
    for k, v in env.items():
        monkeypatch.setenv(k, v)
    clear_settings_cache()
    import app.main as m
    importlib.reload(m)
    return m.app


@pytest.mark.asyncio
async def test_security_headers_present_on_every_response(monkeypatch):
    app = _reload_app(monkeypatch, RATE_LIMIT_ENABLED="false", CSRF_ENABLED="false")
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        r = await c.get("/health")
    assert r.status_code == 200
    for h in ["X-Content-Type-Options", "X-Frame-Options", "Referrer-Policy",
              "Content-Security-Policy", "Cross-Origin-Opener-Policy"]:
        assert h in r.headers, f"missing {h}"
    assert r.headers["X-Content-Type-Options"] == "nosniff"
    assert r.headers["X-Frame-Options"] == "DENY"
    # HSTS only in production
    assert "Strict-Transport-Security" not in r.headers


@pytest.mark.asyncio
async def test_hsts_in_production_only(monkeypatch):
    monkeypatch.setenv("APP_ENV", "production")
    monkeypatch.setenv("COOKIE_SECURE", "true")
    monkeypatch.setenv("TOKEN_ENCRYPTION_KEY", "x" * 44 + "=")
    app = _reload_app(monkeypatch, RATE_LIMIT_ENABLED="true", CSRF_ENABLED="true")
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        r = await c.get("/health")
    assert "Strict-Transport-Security" in r.headers
    assert "max-age=" in r.headers["Strict-Transport-Security"]


@pytest.mark.asyncio
async def test_csrf_blocks_cookie_post_without_token(monkeypatch):
    app = _reload_app(monkeypatch, CSRF_ENABLED="true", RATE_LIMIT_ENABLED="false")
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        # Cookie-authed POST with no CSRF header → 403
        r = await c.post(
            "/api/auth/logout",
            cookies={"mtr_session": "doesnt-matter-for-csrf-check"},
        )
    assert r.status_code == 403
    assert "CSRF" in r.json()["detail"]


@pytest.mark.asyncio
async def test_csrf_allows_bearer_post(monkeypatch):
    """Bearer auth is exempt because browsers don't auto-attach it cross-origin."""
    app = _reload_app(monkeypatch, CSRF_ENABLED="true", RATE_LIMIT_ENABLED="false")
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        r = await c.post(
            "/api/auth/logout",
            headers={"Authorization": "Bearer mtr_at_fake-but-not-csrf-blocked"},
        )
    # 401 (bad token) — NOT 403 (CSRF). That's the contract.
    assert r.status_code != 403


@pytest.mark.asyncio
async def test_csrf_allows_matching_double_submit(monkeypatch):
    app = _reload_app(monkeypatch, CSRF_ENABLED="true", RATE_LIMIT_ENABLED="false")
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        # First GET seeds the cookie
        r0 = await c.get("/health")
        set_cookie = r0.headers.get("set-cookie", "")
        assert "mtr_csrf=" in set_cookie
        token = c.cookies.get("mtr_csrf")
        assert token
        # POST with header matching cookie → not blocked by CSRF
        r = await c.post(
            "/api/auth/logout",
            headers={"X-CSRF-Token": token},
        )
    assert r.status_code != 403


@pytest.mark.asyncio
async def test_csrf_rejects_mismatched_token(monkeypatch):
    app = _reload_app(monkeypatch, CSRF_ENABLED="true", RATE_LIMIT_ENABLED="false")
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        await c.get("/health")  # seed
        r = await c.post("/api/auth/logout", headers={"X-CSRF-Token": "wrong"})
    assert r.status_code == 403


@pytest.mark.asyncio
async def test_rate_limit_blocks_burst_on_auth(monkeypatch):
    app = _reload_app(monkeypatch, RATE_LIMIT_ENABLED="true", CSRF_ENABLED="false")
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        statuses = []
        # Use a non-existent /api/auth/* path: rate-limit middleware runs
        # before routing so 404s still count, and we avoid the live MSAL
        # call that the real /microsoft/login route makes.
        for _ in range(15):
            r = await c.get("/api/auth/_ratelimit_probe")
            statuses.append(r.status_code)
    assert 429 in statuses, f"expected at least one 429 in {statuses}"


@pytest.mark.asyncio
async def test_production_refuses_disabled_csrf(monkeypatch):
    monkeypatch.setenv("APP_ENV", "production")
    monkeypatch.setenv("COOKIE_SECURE", "true")
    monkeypatch.setenv("CSRF_ENABLED", "false")
    monkeypatch.setenv("RATE_LIMIT_ENABLED", "true")
    monkeypatch.setenv("TOKEN_ENCRYPTION_KEY", "x" * 44 + "=")
    clear_settings_cache()
    import app.main as m
    with pytest.raises(RuntimeError, match="CSRF_ENABLED"):
        importlib.reload(m)


def test_security_header_names_helper():
    names = list(header_names())
    assert "X-Content-Type-Options" in names
    assert "Strict-Transport-Security" in names
