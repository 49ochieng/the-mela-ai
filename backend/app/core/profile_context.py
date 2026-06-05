"""
Mela AI – Profile Context Middleware

Enforces the work / personal namespace boundary at the API layer.
Every chat and project endpoint must declare this as a FastAPI dependency.

Hard rules enforced here:
  • X-Profile-Mode must be 'work' or 'personal' ('org' accepted as alias)
  • work mode requires X-Tenant-Id header
  • personal mode must NOT include X-Tenant-Id
  • Server is authoritative — client body values are ignored
"""

from typing import Optional
from dataclasses import dataclass
from fastapi import Header, HTTPException, status

# In dev mode there is no real Entra tenant; the frontend sends this sentinel.
DEV_TENANT_SENTINEL = "dev-tenant-001"


@dataclass(frozen=True)
class ProfileContext:
    """Immutable profile context bound to a single API request."""

    profile_mode: str       # 'work' | 'personal'
    tenant_id: Optional[str]  # required for work, None for personal

    # ── Convenience props ──────────────────────────────────────────────────

    @property
    def is_work(self) -> bool:
        return self.profile_mode == "work"

    @property
    def is_personal(self) -> bool:
        return self.profile_mode == "personal"

    @property
    def db_tenant_id(self) -> Optional[str]:
        """Tenant ID to persist in DB (None for personal records)."""
        return self.tenant_id if self.is_work else None

    # ── Query helpers ──────────────────────────────────────────────────────

    def where_clauses(self, model):
        """
        Return a list of SQLAlchemy column conditions that apply the profile
        namespace filter to *model*.

        Usage::

            stmt = select(Conversation).where(*ctx.where_clauses(Conversation))
        """
        from sqlalchemy import or_
        clauses = [model.profile_mode == self.profile_mode]
        if self.is_work:
            if self.tenant_id == DEV_TENANT_SENTINEL:
                # In dev mode also surface records whose tenant_id was never
                # populated (NULL) — these predate the strict tenant enforcement.
                # The migration in database.py fixes the data; this clause is a
                # belt-and-suspenders for any record that slips through.
                clauses.append(
                    or_(model.tenant_id == self.tenant_id, model.tenant_id.is_(None))
                )
            else:
                clauses.append(model.tenant_id == self.tenant_id)
        else:
            clauses.append(model.tenant_id.is_(None))
        return clauses

    def validate_record(self, record) -> None:
        """
        Raise HTTPException(403) if *record* does not belong to this profile
        context.  Call after fetching a DB record by ID to ensure the caller
        cannot read across profile boundaries.
        """
        if getattr(record, "profile_mode", "personal") != self.profile_mode:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Cross-profile access is not permitted.",
            )
        if self.is_work:
            rec_tenant = getattr(record, "tenant_id", None)
            if rec_tenant != self.tenant_id:
                raise HTTPException(
                    status_code=status.HTTP_403_FORBIDDEN,
                    detail="Cross-tenant access is not permitted.",
                )


# ── Internal helpers ─────────────────────────────────────────────────────────

def _normalize_mode(raw: str) -> str:
    """Normalize profile mode: accept 'org' as legacy alias for 'work'."""
    m = raw.lower().strip()
    return "work" if m == "org" else m


# ── FastAPI dependencies ─────────────────────────────────────────────────────

async def get_profile_context(
    x_profile_mode: str = Header(..., alias="X-Profile-Mode"),
    x_tenant_id: Optional[str] = Header(None, alias="X-Tenant-Id"),
) -> ProfileContext:
    """
    **Required** profile context dependency.

    Returns a validated :class:`ProfileContext` or raises HTTP 400.
    Use on all endpoints that access chats, projects, or messages.
    """
    mode = _normalize_mode(x_profile_mode)

    if mode not in ("work", "personal"):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="X-Profile-Mode must be 'work' or 'personal'.",
        )

    if mode == "work" and not x_tenant_id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="X-Tenant-Id header is required for work profile.",
        )

    if mode == "personal" and x_tenant_id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="X-Tenant-Id must not be provided for personal profile.",
        )

    return ProfileContext(
        profile_mode=mode,
        tenant_id=x_tenant_id if mode == "work" else None,
    )


async def get_optional_profile_context(
    x_profile_mode: Optional[str] = Header(None, alias="X-Profile-Mode"),
    x_tenant_id: Optional[str] = Header(None, alias="X-Tenant-Id"),
) -> ProfileContext:
    """
    **Optional** profile context — falls back to *personal* when headers are
    absent.  Use for admin endpoints or backward-compatible paths.

    Work mode is preserved even when X-Tenant-Id is missing — we use the dev
    sentinel rather than silently downgrading to personal (which was the root
    cause of work chats appearing in personal history).
    """
    if not x_profile_mode:
        # Header-less requests → personal namespace (backward compat)
        return ProfileContext(profile_mode="personal", tenant_id=None)

    mode = _normalize_mode(x_profile_mode)

    if mode not in ("work", "personal"):
        return ProfileContext(profile_mode="personal", tenant_id=None)

    if mode == "work":
        # Use dev sentinel when X-Tenant-Id is absent — do NOT fall back to
        # personal, which would silently store work data in the wrong namespace.
        effective_tenant = x_tenant_id or DEV_TENANT_SENTINEL
        return ProfileContext(profile_mode="work", tenant_id=effective_tenant)

    # personal — tenant_id must be absent (ignore any stale header value)
    return ProfileContext(profile_mode="personal", tenant_id=None)
