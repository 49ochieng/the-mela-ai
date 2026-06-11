"""Phase 2 — production boot-time security validation.

Locks in that the app refuses to start in production with insecure auth
config (cookie not Secure, weak/placeholder JWT secret, missing token
encryption key, etc.). The point is to fail loudly at deploy time rather
than silently shipping an insecure build.
"""
from __future__ import annotations

import pytest

from app.config import clear_settings_cache
from app.main import create_app


@pytest.fixture(autouse=True)
def _isolate(monkeypatch):
    # Required env (always set by these tests; production guard expects them).
    monkeypatch.setenv("FRONTEND_URL", "https://example.com")
    monkeypatch.setenv("BACKEND_URL", "https://api.example.com")
    monkeypatch.setenv("MICROSOFT_REDIRECT_URI", "https://api.example.com/cb")
    monkeypatch.setenv("APP_ENV", "production")
    yield
    clear_settings_cache()


def test_refuses_to_boot_with_insecure_cookie(monkeypatch):
    monkeypatch.setenv("COOKIE_SECURE", "false")
    monkeypatch.setenv("JWT_SECRET", "x" * 64)
    monkeypatch.setenv("SECRET_KEY", "y" * 64)
    monkeypatch.setenv("TOKEN_ENCRYPTION_KEY", "z" * 44)
    clear_settings_cache()
    with pytest.raises(RuntimeError, match="COOKIE_SECURE"):
        create_app()


def test_refuses_to_boot_with_weak_jwt_secret(monkeypatch):
    monkeypatch.setenv("COOKIE_SECURE", "true")
    monkeypatch.setenv("JWT_SECRET", "changeme")
    monkeypatch.setenv("SECRET_KEY", "y" * 64)
    monkeypatch.setenv("TOKEN_ENCRYPTION_KEY", "z" * 44)
    clear_settings_cache()
    with pytest.raises(RuntimeError, match="JWT_SECRET"):
        create_app()


def test_refuses_to_boot_without_token_encryption_key(monkeypatch):
    monkeypatch.setenv("COOKIE_SECURE", "true")
    monkeypatch.setenv("JWT_SECRET", "x" * 64)
    monkeypatch.setenv("SECRET_KEY", "y" * 64)
    monkeypatch.setenv("TOKEN_ENCRYPTION_KEY", "")
    clear_settings_cache()
    with pytest.raises(RuntimeError, match="TOKEN_ENCRYPTION_KEY"):
        create_app()


def test_boots_with_strong_production_config(monkeypatch):
    monkeypatch.setenv("COOKIE_SECURE", "true")
    monkeypatch.setenv("JWT_SECRET", "x" * 64)
    monkeypatch.setenv("SECRET_KEY", "y" * 64)
    monkeypatch.setenv("TOKEN_ENCRYPTION_KEY", "z" * 44)
    monkeypatch.setenv("CSRF_ENABLED", "true")
    monkeypatch.setenv("RATE_LIMIT_ENABLED", "true")
    clear_settings_cache()
    app = create_app()
    assert app is not None
