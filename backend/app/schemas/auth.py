"""
Mela AI - Authentication Schemas
"""

from pydantic import BaseModel, EmailStr
from typing import Optional, List
from datetime import datetime


class TokenData(BaseModel):
    """Token data extracted from JWT."""
    user_id: str
    email: Optional[str] = None
    roles: List[str] = []


class UserInfo(BaseModel):
    """User information from Azure AD token."""
    id: str
    email: str
    name: str
    given_name: Optional[str] = None
    family_name: Optional[str] = None
    roles: List[str] = []
    groups: List[str] = []  # Azure AD group object IDs for ACL filtering
    department: Optional[str] = None
    job_title: Optional[str] = None
    tenant_id: Optional[str] = None


class UserCreate(BaseModel):
    """Schema for creating a user."""
    azure_id: str
    email: EmailStr
    name: str
    department: Optional[str] = None
    job_title: Optional[str] = None
    role: str = "user"


class UserUpdate(BaseModel):
    """Schema for updating a user."""
    name: Optional[str] = None
    department: Optional[str] = None
    job_title: Optional[str] = None
    role: Optional[str] = None
    preferred_model: Optional[str] = None
    daily_token_limit: Optional[int] = None
    is_active: Optional[bool] = None


class UserResponse(BaseModel):
    """User response schema."""
    id: str
    azure_id: str
    email: str
    name: str
    department: Optional[str] = None
    job_title: Optional[str] = None
    role: str
    preferred_model: Optional[str] = None
    daily_token_limit: int
    tokens_used_today: int
    is_active: bool
    bootstrap_elevated_at: Optional[datetime] = None
    created_at: datetime
    updated_at: datetime

    class Config:
        from_attributes = True


class LoginResponse(BaseModel):
    """Login response with user info."""
    user: UserResponse
    welcome_message: str
    tenant_id: Optional[str] = None
