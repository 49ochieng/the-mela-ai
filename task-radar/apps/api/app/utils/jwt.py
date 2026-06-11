"""JWT helpers (Mela Task Radar's own session token, separate from Graph).

Tokens carry the standard hardening claims: ``iss``, ``aud``, ``nbf``, ``iat``,
``exp``, ``sub``, ``tid``, and ``jti``. The ``jti`` is the link to the
server-side ``sessions`` table that lets us revoke individual tokens
("logout") and "sign out everywhere" — which a stateless JWT alone cannot do.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone
from typing import Any

from jose import JWTError, jwt

from ..config import get_settings

JWT_ISSUER = "mela-task-radar"
JWT_AUDIENCE = "mela-task-radar:web"


def create_session_token(
    *,
    user_id: str,
    tenant_id: str,
    jti: str | None = None,
    extra: dict[str, Any] | None = None,
) -> tuple[str, str, datetime]:
    """Mint a session JWT.

    Returns ``(token, jti, expires_at)`` so the caller can persist a row in
    the ``sessions`` table keyed by ``jti``.
    """
    s = get_settings()
    now = datetime.now(timezone.utc)
    exp = now + timedelta(minutes=s.access_token_expire_minutes)
    jti_val = jti or str(uuid.uuid4())
    payload: dict[str, Any] = {
        "iss": JWT_ISSUER,
        "aud": JWT_AUDIENCE,
        "sub": user_id,
        "tid": tenant_id,
        "jti": jti_val,
        "iat": int(now.timestamp()),
        "nbf": int(now.timestamp()),
        "exp": int(exp.timestamp()),
    }
    if extra:
        payload.update(extra)
    token = jwt.encode(payload, s.jwt_secret, algorithm=s.jwt_algorithm)
    return token, jti_val, exp


def decode_session_token(token: str) -> dict[str, Any]:
    """Verify a session JWT against the primary key, then any secondaries.

    ``JWT_SECRET`` is the only key used for *signing*. ``JWT_SECRETS_SECONDARY``
    (comma-separated) lists older keys still accepted for *verification*. To
    rotate, set the new key as ``JWT_SECRET`` and move the old one into the
    secondary list for the duration of the longest-lived session (≤ 8h);
    after the grace window passes, drop it.
    """
    s = get_settings()
    candidates: list[str] = [s.jwt_secret]
    secondary = (s.jwt_secrets_secondary or "").strip()
    if secondary:
        for k in secondary.split(","):
            k = k.strip()
            if k:
                candidates.append(k)
    last_err: Exception | None = None
    for key in candidates:
        try:
            return jwt.decode(
                token,
                key,
                algorithms=[s.jwt_algorithm],
                audience=JWT_AUDIENCE,
                issuer=JWT_ISSUER,
            )
        except JWTError as e:
            last_err = e
            continue
    raise ValueError(f"Invalid token: {last_err}")
