"""Phase 6 — key rotation: dual JWT keys + MultiFernet token references."""
from __future__ import annotations

import importlib

from cryptography.fernet import Fernet


def _reload_settings(monkeypatch, **env: str):
    for k, v in env.items():
        monkeypatch.setenv(k, v)
    from app import config as cfg
    cfg.clear_settings_cache()
    return cfg.get_settings()


def test_jwt_secondary_key_accepted_for_verification(monkeypatch):
    primary = "p" * 48
    secondary = "s" * 48
    _reload_settings(monkeypatch, JWT_SECRET=secondary)
    from app.utils.jwt import create_session_token
    # Mint a token with the OLD key (now "secondary").
    token, _, _ = create_session_token(user_id="u1", tenant_id="t1")

    # Now rotate: promote new primary, keep old as secondary.
    _reload_settings(monkeypatch, JWT_SECRET=primary, JWT_SECRETS_SECONDARY=secondary)
    import app.utils.jwt as jmod
    importlib.reload(jmod)
    payload = jmod.decode_session_token(token)
    assert payload["sub"] == "u1"

    # And after dropping the secondary, the old token must fail.
    _reload_settings(monkeypatch, JWT_SECRET=primary, JWT_SECRETS_SECONDARY="")
    importlib.reload(jmod)
    try:
        jmod.decode_session_token(token)
    except ValueError:
        return
    raise AssertionError("expected ValueError after dropping secondary key")


def test_fernet_secondary_key_decrypts_old_references(monkeypatch):
    old = Fernet.generate_key().decode()
    new = Fernet.generate_key().decode()

    # Write a reference with the OLD key.
    _reload_settings(monkeypatch, TOKEN_ENCRYPTION_KEY=old, TOKEN_ENCRYPTION_KEYS_SECONDARY="")
    import app.services.auth.token_store as ts
    importlib.reload(ts)
    from datetime import datetime, timezone
    store_old = ts.FernetTokenStore()
    ref = store_old.put("k", ts.StoredToken(
        access_token="A", refresh_token="R",
        expires_at=datetime.now(timezone.utc), scopes=[]))

    # Rotate: new primary, old as secondary. The reference must still decrypt.
    _reload_settings(monkeypatch, TOKEN_ENCRYPTION_KEY=new, TOKEN_ENCRYPTION_KEYS_SECONDARY=old)
    importlib.reload(ts)
    store_new = ts.FernetTokenStore()
    decrypted = store_new.get(ref)
    assert decrypted.access_token == "A"

    # Re-encrypt the reference with the new primary, then drop the secondary.
    rotated = store_new.rotate(ref)
    _reload_settings(monkeypatch, TOKEN_ENCRYPTION_KEY=new, TOKEN_ENCRYPTION_KEYS_SECONDARY="")
    importlib.reload(ts)
    final = ts.FernetTokenStore()
    assert final.get(rotated).access_token == "A"
    # And the original (still encrypted with the retired key) no longer decrypts.
    try:
        final.get(ref)
    except ValueError:
        return
    raise AssertionError("expected ValueError after dropping retired key")
