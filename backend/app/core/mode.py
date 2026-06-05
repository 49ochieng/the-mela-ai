"""
Mela AI - Mode Controller

Centralized personal/work mode policy for request routing and hard boundaries.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal, Optional

from fastapi import Depends, HTTPException, Request, status

from app.core.profile_context import ProfileContext, get_profile_context
from app.core.security import get_current_user
from app.schemas.auth import UserInfo

Mode = Literal["personal", "work"]


@dataclass(frozen=True)
class UserSession:
    mode: Mode
    user_id: str
    tenant_id: Optional[str] = None
    # Raw bearer token the caller presented (the user assertion). Used by
    # the Microsoft Graph OBO flow (Phase 5 / CR-2) so LLM-initiated
    # Graph calls run with the user's delegated permissions. Optional so
    # background workflows can still construct UserSession without one.
    access_token: Optional[str] = None

    @property
    def enterprise_enabled(self) -> bool:
        return self.mode == "work" and bool(self.tenant_id)

    @property
    def is_personal(self) -> bool:
        return self.mode == "personal"

    @property
    def is_work(self) -> bool:
        return self.mode == "work"


_ENTERPRISE_INTENT_KEYWORDS = (
    "sharepoint",
    "onedrive",
    "microsoft graph",
    "graph api",
    "internal file",
    "internal files",
    "internal document",
    "internal docs",
    "company report",
    "company files",
    "company documents",
    "organization documents",
    "organisation documents",
    "my company files",
    "my company docs",
    "my internal docs",
    "enterprise",
    "planner",
    "intranet",
)


def build_user_session(
    profile_ctx: ProfileContext,
    user: UserInfo,
    access_token: Optional[str] = None,
) -> UserSession:
    mode: Mode = "work" if profile_ctx.is_work else "personal"
    return UserSession(
        mode=mode,
        user_id=str(getattr(user, "id", "")),
        tenant_id=profile_ctx.db_tenant_id if mode == "work" else None,
        access_token=access_token,
    )


def includes_enterprise_data_intent(message: str) -> bool:
    text = (message or "").lower()
    return any(k in text for k in _ENTERPRISE_INTENT_KEYWORDS)


def enforce_personal_mode_boundaries(session: UserSession, message: str) -> None:
    if session.is_personal and includes_enterprise_data_intent(message):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Enterprise data access is not allowed in Personal mode.",
        )


async def get_user_session(
    request: Request,
    profile_ctx: ProfileContext = Depends(get_profile_context),
    current_user: UserInfo = Depends(get_current_user),
) -> UserSession:
    # get_current_user stashes the raw bearer on request.state.access_token —
    # forward it so downstream Graph tools can run OBO when the feature
    # flag is on.
    token = getattr(request.state, "access_token", None)
    return build_user_session(profile_ctx, current_user, access_token=token)


async def require_work_session(
    session: UserSession = Depends(get_user_session),
) -> UserSession:
    if not session.enterprise_enabled:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="This endpoint is only available in Work mode.",
        )
    return session
