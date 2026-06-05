"""
Mela AI - Collaboration Schemas (membership, invites, share links)
"""

from pydantic import BaseModel, ConfigDict, field_validator
from typing import Optional, Literal
from datetime import datetime
import enum


class MemberRole(str, enum.Enum):
    OWNER = "owner"
    EDITOR = "editor"
    VIEWER = "viewer"


class InviteStatus(str, enum.Enum):
    PENDING = "pending"
    ACCEPTED = "accepted"
    REVOKED = "revoked"
    EXPIRED = "expired"


# ── Member schemas ─────────────────────────────────────────────────────────────

class MemberResponse(BaseModel):
    id: str
    user_id: str
    user_email: str
    user_name: str
    role: MemberRole
    added_by: str
    added_at: datetime
    model_config = ConfigDict(from_attributes=True)


class AddMemberRequest(BaseModel):
    email: str
    role: MemberRole = MemberRole.VIEWER

    @field_validator("role")
    @classmethod
    def role_not_owner(cls, v: MemberRole) -> MemberRole:
        """Callers cannot assign owner role directly via invite."""
        if v == MemberRole.OWNER:
            raise ValueError("Cannot assign owner role via invite; transfer ownership separately.")
        return v


class UpdateMemberRoleRequest(BaseModel):
    role: MemberRole

    @field_validator("role")
    @classmethod
    def role_not_owner(cls, v: MemberRole) -> MemberRole:
        if v == MemberRole.OWNER:
            raise ValueError("Cannot assign owner role via role update; transfer ownership separately.")
        return v


# ── Invite schemas ─────────────────────────────────────────────────────────────

class InviteResponse(BaseModel):
    id: str
    resource_type: str
    resource_id: str
    inviter_user_id: str
    invitee_email: str
    invitee_user_id: Optional[str] = None
    role: MemberRole
    status: InviteStatus
    created_at: datetime
    expires_at: Optional[datetime] = None
    model_config = ConfigDict(from_attributes=True)


# ── Share link schemas (policy-gated, disabled by default) ─────────────────────

class CreateShareLinkRequest(BaseModel):
    resource_type: Literal["project", "chat"]
    resource_id: str
    permission_scope: MemberRole = MemberRole.VIEWER
    expires_at: Optional[datetime] = None


class ShareLinkResponse(BaseModel):
    id: str
    resource_type: str
    resource_id: str
    created_by: str
    permission_scope: MemberRole
    expires_at: Optional[datetime] = None
    revoked_at: Optional[datetime] = None
    created_at: datetime
    model_config = ConfigDict(from_attributes=True)
