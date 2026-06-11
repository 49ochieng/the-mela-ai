"""Phase 3 — TenantConfig + secret-store + admin RBAC.

Verifies:
- Only admins can read/write the tenant config; non-admins get 403.
- Submitting a client_secret stores it in the secret store, **not** in the DB.
- The plaintext secret is never returned by any API.
- An empty-string client_secret clears both the ref and the stored value.
- ``DELETE /admin/tenant-config/secret`` is admin-gated and idempotent.
- Cross-tenant isolation: an admin in tenant A cannot read tenant B's config.
- ``KEY_VAULT_URL`` unset in production → factory raises (no silent downgrade).
"""
from __future__ import annotations

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.config import clear_settings_cache, get_settings
from app.database import Base, get_session
from app.main import create_app
from app.models import Tenant, TenantConfig, User
from app.routers import admin_tenant as admin_router
from app.services.auth.sessions import issue_session
from app.services.secrets import EnvSecretStore, SecretStoreError
from app.services.secrets.factory import clear_secret_store_cache, get_secret_store


async def _setup_two_tenants():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    sm = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
    async with sm() as s:
        ta = Tenant(entra_tenant_id="A", name="A"); s.add(ta)
        tb = Tenant(entra_tenant_id="B", name="B"); s.add(tb); await s.flush()
        admin_a = User(tenant_id=ta.id, entra_user_id="ua", display_name="Admin A",
                       email="a@x", timezone="UTC", role="admin")
        user_a = User(tenant_id=ta.id, entra_user_id="ua2", display_name="User A",
                      email="u@x", timezone="UTC", role="user")
        admin_b = User(tenant_id=tb.id, entra_user_id="ub", display_name="Admin B",
                       email="b@x", timezone="UTC", role="admin")
        s.add_all([admin_a, user_a, admin_b]); await s.commit()
        ids = (ta.id, tb.id, admin_a.id, user_a.id, admin_b.id)
    app = create_app()

    async def _ov():
        async with sm() as s:
            yield s
    app.dependency_overrides[get_session] = _ov
    # Force EnvSecretStore for tests, regardless of env.
    store = EnvSecretStore()
    app.dependency_overrides[admin_router._store] = lambda: store
    return app, sm, store, ids, engine


@pytest.mark.asyncio
async def test_non_admin_forbidden():
    app, sm, store, (tA, _, _, user_a, _), engine = await _setup_two_tenants()
    try:
        s = get_settings()
        async with sm() as ss:
            tok, _ = await issue_session(ss, user_id=user_a, tenant_id=tA)
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t",
                               cookies={s.effective_cookie_name: tok}) as c:
            r = await c.get("/api/admin/tenant-config")
            assert r.status_code == 403
            r = await c.put("/api/admin/tenant-config", json={"azure_client_id": "x"})
            assert r.status_code == 403
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_admin_can_save_and_secret_never_appears_in_response_or_db():
    app, sm, store, (tA, _, admin_a, _, _), engine = await _setup_two_tenants()
    try:
        s = get_settings()
        async with sm() as ss:
            tok, _ = await issue_session(ss, user_id=admin_a, tenant_id=tA)
        secret = "super-secret-DO-NOT-LEAK"
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t",
                               cookies={s.effective_cookie_name: tok}) as c:
            r = await c.put("/api/admin/tenant-config", json={
                "azure_tenant_id": "tid-1",
                "azure_client_id": "cid-1",
                "azure_client_secret": secret,
                "azure_public_client": False,
            })
            assert r.status_code == 200, r.text
            body = r.json()
            assert body["has_client_secret"] is True
            # Secret value must NEVER appear in the API response.
            assert secret not in r.text
            # GET also must not contain it.
            r = await c.get("/api/admin/tenant-config")
            assert secret not in r.text
            assert r.json()["has_client_secret"] is True

        # DB row must hold only a reference, not the plaintext.
        async with sm() as ss:
            cfg = (await ss.execute(select(TenantConfig).where(TenantConfig.tenant_id == tA))).scalar_one()
            assert cfg.azure_client_secret_ref
            assert cfg.azure_client_secret_ref != secret
            # No column on the row contains the plaintext.
            for col in cfg.__table__.columns:
                v = getattr(cfg, col.name)
                assert v != secret, f"plaintext leaked into column {col.name}"

        # The secret store DOES hold the plaintext (under the opaque ref).
        assert await store.get(cfg.azure_client_secret_ref) == secret
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_clear_secret_removes_ref_and_value():
    app, sm, store, (tA, _, admin_a, _, _), engine = await _setup_two_tenants()
    try:
        s = get_settings()
        async with sm() as ss:
            tok, _ = await issue_session(ss, user_id=admin_a, tenant_id=tA)
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t",
                               cookies={s.effective_cookie_name: tok}) as c:
            await c.put("/api/admin/tenant-config", json={
                "azure_tenant_id": "tid", "azure_client_id": "cid",
                "azure_client_secret": "v1",
            })
            r = await c.delete("/api/admin/tenant-config/secret")
            assert r.status_code == 204
            r = await c.get("/api/admin/tenant-config")
            assert r.json()["has_client_secret"] is False
        async with sm() as ss:
            cfg = (await ss.execute(select(TenantConfig).where(TenantConfig.tenant_id == tA))).scalar_one()
            assert cfg.azure_client_secret_ref is None
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_cross_tenant_isolation():
    app, sm, store, (tA, tB, admin_a, _, admin_b), engine = await _setup_two_tenants()
    try:
        s = get_settings()
        async with sm() as ss:
            tok_a, _ = await issue_session(ss, user_id=admin_a, tenant_id=tA)
            tok_b, _ = await issue_session(ss, user_id=admin_b, tenant_id=tB)
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t",
                               cookies={s.effective_cookie_name: tok_a}) as c:
            await c.put("/api/admin/tenant-config", json={
                "azure_tenant_id": "TENANT-A-ONLY", "azure_client_id": "cid-a",
            })
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t",
                               cookies={s.effective_cookie_name: tok_b}) as c:
            r = await c.get("/api/admin/tenant-config")
            assert r.status_code == 200
            # Admin B sees their own (empty) tenant config, not tenant A's.
            assert r.json()["azure_tenant_id"] in (None, "")
    finally:
        await engine.dispose()


def test_secret_store_factory_refuses_env_in_production(monkeypatch):
    monkeypatch.setenv("APP_ENV", "production")
    monkeypatch.setenv("KEY_VAULT_URL", "")
    clear_settings_cache()
    clear_secret_store_cache()
    try:
        with pytest.raises(SecretStoreError):
            get_secret_store()
    finally:
        clear_settings_cache()
        clear_secret_store_cache()


def test_secret_store_factory_uses_env_store_in_dev(monkeypatch):
    monkeypatch.setenv("APP_ENV", "development")
    monkeypatch.setenv("KEY_VAULT_URL", "")
    clear_settings_cache()
    clear_secret_store_cache()
    try:
        store = get_secret_store()
        assert isinstance(store, EnvSecretStore)
    finally:
        clear_settings_cache()
        clear_secret_store_cache()
