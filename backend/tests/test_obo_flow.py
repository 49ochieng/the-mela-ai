"""
Phase 5 / CR-2 — Microsoft Graph On-Behalf-Of flow tests.

Covers:
  * Flag off → app-only fallback.
  * No assertion → app-only fallback.
  * Successful OBO acquisition + per-(oid, scopes) cache.
  * Cache hit on second call (MSAL only invoked once).
  * Different scope sets → distinct cache entries.
  * MSAL failure → returns None.
  * ``_parse_user_oid_from_assertion`` recovers the oid claim from an
    unsigned JWT payload (we don't verify here — upstream auth does).
  * ``clear_obo_cache`` clears both the app-only and OBO caches.
"""

import base64
import json
from typing import Any, Dict

import pytest

from app.core.config import settings
from app.services import obo_service


def _make_jwt(payload: Dict[str, Any]) -> str:
    """Build an unsigned JWT-shaped string for the OID parser."""
    def _b64(d: Dict[str, Any]) -> str:
        raw = json.dumps(d).encode("utf-8")
        return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")

    header = _b64({"alg": "none", "typ": "JWT"})
    body = _b64(payload)
    return f"{header}.{body}.sig"


@pytest.fixture(autouse=True)
def _reset_state(monkeypatch):
    """Clear caches and force OBO-friendly Entra config for each test."""
    obo_service.clear_obo_cache()
    monkeypatch.setattr(
        type(settings), "effective_client_id",
        property(lambda self: "test-client"),
    )
    monkeypatch.setattr(
        type(settings), "effective_client_secret",
        property(lambda self: "test-secret"),
    )
    monkeypatch.setattr(
        type(settings), "graph_authority",
        property(lambda self: "https://login.microsoftonline.com/test-tenant"),
    )
    yield
    obo_service.clear_obo_cache()


class _FakeMsalApp:
    """Records calls + returns canned MSAL responses keyed by scope set."""

    instances: list["_FakeMsalApp"] = []

    def __init__(self, *_, **__):
        self.obo_calls: list[Dict[str, Any]] = []
        self.app_only_calls: list[Any] = []
        _FakeMsalApp.instances.append(self)

    # response queue — tests prime it before invoking.
    obo_responses: list[Dict[str, Any]] = []

    def acquire_token_on_behalf_of(self, user_assertion: str, scopes):
        self.obo_calls.append(
            {"assertion": user_assertion, "scopes": list(scopes)}
        )
        if _FakeMsalApp.obo_responses:
            return _FakeMsalApp.obo_responses.pop(0)
        return {"access_token": "obo-token", "expires_in": 3600}

    def acquire_token_for_client(self, scopes):
        self.app_only_calls.append(list(scopes))
        return {"access_token": "app-only-token", "expires_in": 3600}


@pytest.fixture
def fake_msal(monkeypatch):
    """Patch msal.ConfidentialClientApplication with the fake double."""
    _FakeMsalApp.instances = []
    _FakeMsalApp.obo_responses = []
    monkeypatch.setattr(
        obo_service.msal, "ConfidentialClientApplication", _FakeMsalApp,
    )
    return _FakeMsalApp


# ── Flag / fallback semantics ─────────────────────────────────────────────


@pytest.mark.asyncio
async def test_flag_off_uses_app_only(fake_msal, monkeypatch):
    monkeypatch.setattr(settings, "USE_OBO_FOR_GRAPH", False, raising=False)
    tok = await obo_service.get_graph_token_obo(
        user_assertion=_make_jwt({"oid": "user-1"}),
        scopes=["Mail.Send"],
    )
    assert tok == "app-only-token"
    # No OBO call ever made.
    assert not any(inst.obo_calls for inst in fake_msal.instances)


@pytest.mark.asyncio
async def test_missing_assertion_uses_app_only(fake_msal, monkeypatch):
    monkeypatch.setattr(settings, "USE_OBO_FOR_GRAPH", True, raising=False)
    tok = await obo_service.get_graph_token_obo(
        user_assertion="", scopes=["Mail.Send"],
    )
    assert tok == "app-only-token"
    assert not any(inst.obo_calls for inst in fake_msal.instances)


# ── OBO acquisition + caching ────────────────────────────────────────────


@pytest.mark.asyncio
async def test_obo_acquired_and_cached(fake_msal, monkeypatch):
    monkeypatch.setattr(settings, "USE_OBO_FOR_GRAPH", True, raising=False)
    assertion = _make_jwt({"oid": "alice-oid"})
    scopes = ["https://graph.microsoft.com/Mail.Send"]

    tok1 = await obo_service.get_graph_token_obo(
        user_assertion=assertion, scopes=scopes,
    )
    tok2 = await obo_service.get_graph_token_obo(
        user_assertion=assertion, scopes=scopes,
    )

    assert tok1 == "obo-token" == tok2
    obo_calls = [c for inst in fake_msal.instances for c in inst.obo_calls]
    assert len(obo_calls) == 1, "second call should hit the cache"


@pytest.mark.asyncio
async def test_different_scope_sets_have_distinct_cache(fake_msal, monkeypatch):
    monkeypatch.setattr(settings, "USE_OBO_FOR_GRAPH", True, raising=False)
    assertion = _make_jwt({"oid": "bob-oid"})

    _FakeMsalApp.obo_responses = [
        {"access_token": "mail-token", "expires_in": 3600},
        {"access_token": "cal-token", "expires_in": 3600},
    ]
    mail_tok = await obo_service.get_graph_token_obo(
        user_assertion=assertion, scopes=["Mail.Send"],
    )
    cal_tok = await obo_service.get_graph_token_obo(
        user_assertion=assertion, scopes=["Calendars.ReadWrite"],
    )

    assert mail_tok == "mail-token"
    assert cal_tok == "cal-token"
    obo_calls = [c for inst in fake_msal.instances for c in inst.obo_calls]
    assert len(obo_calls) == 2


# ── Failure handling ─────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_msal_error_returns_none(fake_msal, monkeypatch):
    monkeypatch.setattr(settings, "USE_OBO_FOR_GRAPH", True, raising=False)
    _FakeMsalApp.obo_responses = [
        {"error": "invalid_grant", "error_description": "expired"},
    ]
    tok = await obo_service.get_graph_token_obo(
        user_assertion=_make_jwt({"oid": "carol-oid"}),
        scopes=["Mail.Send"],
    )
    assert tok is None


# ── Helpers ──────────────────────────────────────────────────────────────


def test_parse_user_oid_handles_oid_sub_upn():
    assert (
        obo_service._parse_user_oid_from_assertion(
            _make_jwt({"oid": "the-oid"})
        )
        == "the-oid"
    )
    assert (
        obo_service._parse_user_oid_from_assertion(
            _make_jwt({"sub": "the-sub"})
        )
        == "the-sub"
    )
    assert (
        obo_service._parse_user_oid_from_assertion(
            _make_jwt({"preferred_username": "u@x"})
        )
        == "u@x"
    )
    assert obo_service._parse_user_oid_from_assertion("not.a.jwt") is None
    assert obo_service._parse_user_oid_from_assertion("") is None


def test_scope_hash_is_order_and_case_insensitive():
    a = obo_service._scope_hash(["Mail.Send", "Calendars.Read"])
    b = obo_service._scope_hash(["calendars.read", "mail.send"])
    c = obo_service._scope_hash(["Mail.Send"])
    assert a == b
    assert a != c


@pytest.mark.asyncio
async def test_clear_obo_cache_resets_both_caches(fake_msal, monkeypatch):
    monkeypatch.setattr(settings, "USE_OBO_FOR_GRAPH", True, raising=False)
    assertion = _make_jwt({"oid": "dave-oid"})
    await obo_service.get_graph_token_obo(
        user_assertion=assertion, scopes=["Mail.Send"],
    )
    # Prime app-only cache too.
    obo_service._cached_token = ("old-app-token", 9999999999.0)
    assert obo_service._obo_local_cache  # populated
    obo_service.clear_obo_cache()
    assert obo_service._cached_token is None
    assert obo_service._obo_local_cache == {}
