"""
Sprint 3.3 — Sensitivity-label enforcement.

Maps text sensitivity labels (Microsoft Purview / AIP style) to a numeric
ladder and per-role ceilings. When ``ENFORCE_SENSITIVITY_LABELS=true`` the
query pipeline drops chunks above the caller's role ceiling.

Ladder:
    public(0) < internal(1) < confidential(2) < highly_confidential(3) < restricted(4)

Defaults (per-role ceiling):
    read_only_user / standard_user / user  →  1  (internal)
    power_user                              →  2  (confidential)
    tenant_admin                            →  3  (highly_confidential)
    platform_admin / admin                  →  4  (restricted)
    service_account                         →  1  (internal — no escalation)
    viewer                                  →  0  (public only)
"""

from __future__ import annotations


# Numeric ladder. Higher = more sensitive.
SENSITIVITY_LEVELS = {
    "public": 0,
    "internal": 1,
    "confidential": 2,
    "highly confidential": 3,
    "highly_confidential": 3,
    "restricted": 4,
    "secret": 4,
}


_ROLE_CEILINGS = {
    "viewer": 0,
    "read_only_user": 1,
    "user": 1,
    "standard_user": 1,
    "service_account": 1,
    "power_user": 2,
    "tenant_admin": 3,
    "platform_admin": 4,
    "admin": 4,
}


def normalise_label(label: str | None) -> int:
    """Map a free-text label to a numeric level.

    Unknown / empty labels return 0 (public). Matching is case-insensitive
    and tolerant of underscores vs spaces.
    """
    if not label:
        return 0
    key = label.strip().lower().replace("-", " ")
    return SENSITIVITY_LEVELS.get(key, 0)


def max_sensitivity_for_role(role: str | None) -> int:
    """Return the highest sensitivity level a role can see.

    Unknown roles default to ``standard_user`` (level 1).
    """
    if not role:
        return 1
    return _ROLE_CEILINGS.get(str(role).strip().lower(), 1)
