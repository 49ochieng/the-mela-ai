"""Phase 5.1: log message redaction."""
from __future__ import annotations

from app.logging_config import _redact


def test_redacts_bearer_token():
    out = _redact("user signed in with Authorization: Bearer eyJabcDEFghijklmn1234")
    assert "eyJabcDEFghijklmn1234" not in out
    assert "Bearer ***" in out or "Authorization: ***" in out


def test_redacts_agent_token_format():
    out = _redact("calling MCP with mtr_at_abcd1234efgh5678")
    assert "mtr_at_abcd1234efgh5678" not in out
    assert "***" in out


def test_redacts_jwt_in_freeform_text():
    jwt = "eyJ" + "A" * 80
    out = _redact(f"decoded token = {jwt}")
    assert jwt not in out


def test_redacts_fernet_reference():
    out = _redact("token_reference=f1:gAAAAABabcdefghijklmnopqrstuvwxyz0123456789")
    assert "f1:gAAAAABabcdefghijklmnopqrstuvwxyz" not in out


def test_redacts_keyed_secret_payloads():
    out = _redact("client_secret=Sup3rSecretKey1234 azure_client_secret: Tota11ySecret")
    assert "Sup3rSecretKey1234" not in out
    assert "Tota11ySecret" not in out


def test_passthrough_safe_text():
    safe = "scan finished: 42 messages, 5 tasks created"
    assert _redact(safe) == safe
