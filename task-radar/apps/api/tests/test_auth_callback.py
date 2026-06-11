"""End-to-end auth callback contract tests.

Locks in:
- /api/auth/microsoft/login starts a PKCE flow and 307-redirects to Microsoft
- /api/auth/microsoft/callback success path:
    * upserts tenant/user/graph_connection
    * sets the session cookie
    * 302-redirects to FRONTEND_URL/dashboard
- /api/auth/microsoft/callback failure paths:
    * provider error -> redirect to /auth/error with reason
    * invalid state -> redirect to /auth/error with reason
    * MSAL token exchange failure -> redirect to /auth/error
- /api/me works with the cookie set by the callback
- AZURE_PUBLIC_CLIENT toggle picks the right MSAL app type
"""
from __future__ import annotations

from urllib.parse import parse_qs, urlparse

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.config import get_settings
from app.database import Base, get_session
from app.main import create_app
from app.routers import auth as auth_router
from app.services.auth import entra as entra_module
from app.services.auth.oauth_state import put_state


async def _fresh_app(monkeypatch: pytest.MonkeyPatch | None = None):
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    sm = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
    app = create_app()

    async def _override():
        async with sm() as s:
            yield s

    app.dependency_overrides[get_session] = _override
    return app, sm, engine


def _fake_token_response() -> dict:
    return {
        "access_token": "fake-access",
        "refresh_token": "fake-refresh",
        "expires_in": 3600,
        "scope": "openid profile offline_access User.Read Mail.Read",
        "id_token_claims": {
            "oid": "user-oid-1",
            "tid": "tenant-tid-1",
            "preferred_username": "alice@contoso.com",
            "name": "Alice Example",
        },
    }


@pytest.mark.asyncio
async def test_login_redirects_to_microsoft(monkeypatch: pytest.MonkeyPatch):
    """The login route must initiate a PKCE flow and 307 to login.microsoftonline.com."""
    def fake_initiate(redirect_uri: str | None = None):
        return {
            "state": "test-state-1",
            "auth_uri": "https://login.microsoftonline.com/common/oauth2/v2.0/authorize?state=test-state-1",
            "code_verifier": "v",
        }

    monkeypatch.setattr(auth_router, "initiate_auth_code_flow", fake_initiate)
    app, sm, engine = await _fresh_app()
    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
            r = await c.get("/api/auth/microsoft/login", follow_redirects=False)
        assert r.status_code == 307
        assert "login.microsoftonline.com" in r.headers["location"]
        # State row persisted in oauth_states table
        from sqlalchemy import select
        from app.models import OAuthState
        async with sm() as ss:
            row = (await ss.execute(select(OAuthState).where(OAuthState.state == "test-state-1"))).scalar_one_or_none()
            assert row is not None
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_callback_success_sets_cookie_and_redirects_to_dashboard(monkeypatch: pytest.MonkeyPatch):
    s = get_settings()

    def fake_acquire(flow, params):
        assert params.get("state") == "good-state"
        assert params.get("code") == "the-code"
        return _fake_token_response()

    monkeypatch.setattr(auth_router, "acquire_token_by_auth_code_flow", fake_acquire)

    app, sm, engine = await _fresh_app()
    async with sm() as ss:
        await put_state(ss, "good-state", {"state": "good-state", "code_verifier": "v"})
    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
            r = await c.get(
                "/api/auth/microsoft/callback",
                params={"code": "the-code", "state": "good-state"},
                follow_redirects=False,
            )
        assert r.status_code == 302
        assert r.headers["location"] == f"{s.frontend_url}/dashboard"
        # Session cookie set
        cookies = r.headers.get_list("set-cookie")
        assert any(s.effective_cookie_name in c for c in cookies), cookies
        # /api/me should now work with that cookie
        cookie_header = next(c for c in cookies if c.startswith(f"{s.effective_cookie_name}="))
        token_value = cookie_header.split(";", 1)[0].split("=", 1)[1]
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://t",
            cookies={s.effective_cookie_name: token_value},
        ) as c:
            me = await c.get("/api/me")
        assert me.status_code == 200
        body = me.json()
        assert body["email"] == "alice@contoso.com"
        assert body["display_name"] == "Alice Example"
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_callback_provider_error_redirects_to_error_page():
    s = get_settings()
    app, sm, engine = await _fresh_app()
    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
            r = await c.get(
                "/api/auth/microsoft/callback",
                params={"error": "access_denied", "error_description": "User cancelled"},
                follow_redirects=False,
            )
        assert r.status_code == 302
        loc = r.headers["location"]
        assert loc.startswith(f"{s.frontend_url}/auth/error?reason=")
        reason = parse_qs(urlparse(loc).query)["reason"][0]
        assert "User cancelled" in reason
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_callback_invalid_state_redirects_to_error_page():
    s = get_settings()
    app, sm, engine = await _fresh_app()
    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
            r = await c.get(
                "/api/auth/microsoft/callback",
                params={"code": "x", "state": "unknown-state"},
                follow_redirects=False,
            )
        assert r.status_code == 302
        assert r.headers["location"].startswith(f"{s.frontend_url}/auth/error?reason=")
        reason = parse_qs(urlparse(r.headers["location"]).query)["reason"][0]
        assert "state" in reason.lower()
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_callback_token_exchange_failure_redirects_to_error_page(monkeypatch: pytest.MonkeyPatch):
    s = get_settings()

    def fake_acquire(flow, params):
        raise RuntimeError("OAuth failure: AADSTS700025: Client is public")

    monkeypatch.setattr(auth_router, "acquire_token_by_auth_code_flow", fake_acquire)

    app, sm, engine = await _fresh_app()
    async with sm() as ss:
        await put_state(ss, "bad-state", {"state": "bad-state", "code_verifier": "v"})
    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
            r = await c.get(
                "/api/auth/microsoft/callback",
                params={"code": "x", "state": "bad-state"},
                follow_redirects=False,
            )
        assert r.status_code == 302
        loc = r.headers["location"]
        assert loc.startswith(f"{s.frontend_url}/auth/error?reason=")
        reason = parse_qs(urlparse(loc).query)["reason"][0]
        assert "AADSTS700025" in reason
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_unauthenticated_me_returns_401():
    app, sm, engine = await _fresh_app()
    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
            r = await c.get("/api/me")
        assert r.status_code == 401
    finally:
        await engine.dispose()


def test_msal_app_type_switches_with_public_client_flag(monkeypatch: pytest.MonkeyPatch):
    """AZURE_PUBLIC_CLIENT=true (or missing secret) -> public client mode.

    We assert on ``_is_public_client()`` rather than actually constructing the
    MSAL app, because MSAL hits the network during ``__init__`` to discover the
    authority's OIDC config.
    """
    from app import config as cfg_mod

    cfg_mod.get_settings.cache_clear()
    monkeypatch.setenv("AZURE_PUBLIC_CLIENT", "true")
    monkeypatch.setenv("AZURE_CLIENT_ID", "test-client")
    monkeypatch.setenv("AZURE_CLIENT_SECRET", "ignored")
    cfg_mod.get_settings.cache_clear()
    assert entra_module._is_public_client() is True

    monkeypatch.setenv("AZURE_PUBLIC_CLIENT", "false")
    monkeypatch.setenv("AZURE_CLIENT_SECRET", "a-secret")
    cfg_mod.get_settings.cache_clear()
    assert entra_module._is_public_client() is False

    # Missing secret -> auto public mode
    monkeypatch.setenv("AZURE_PUBLIC_CLIENT", "false")
    monkeypatch.setenv("AZURE_CLIENT_SECRET", "")
    cfg_mod.get_settings.cache_clear()
    assert entra_module._is_public_client() is True

    cfg_mod.get_settings.cache_clear()
