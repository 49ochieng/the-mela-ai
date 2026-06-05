"""
Mela AI - Admin Schemas
"""

from pydantic import BaseModel
from typing import Optional, List, Dict, Any
from datetime import datetime, date


class UsageStats(BaseModel):
    """Usage statistics."""
    total_users: int
    active_users_today: int
    total_conversations: int
    total_messages: int
    total_tokens_used: int
    total_documents: int
    indexed_documents: int


class DailyUsage(BaseModel):
    """Daily usage statistics."""
    date: date
    users: int
    conversations: int
    messages: int
    tokens: int


class ModelUsageStats(BaseModel):
    """Model-specific usage."""
    model: str
    request_count: int
    total_tokens: int
    prompt_tokens: int
    completion_tokens: int


class UserUsageStats(BaseModel):
    """Per-user usage statistics."""
    user_id: str
    user_name: str
    user_email: str
    total_conversations: int
    total_messages: int
    total_tokens: int
    last_active: Optional[datetime] = None


class AnalyticsResponse(BaseModel):
    """Analytics dashboard response."""
    overview: UsageStats
    daily_usage: List[DailyUsage]
    model_usage: List[ModelUsageStats]
    top_users: List[UserUsageStats]


class AuditLogResponse(BaseModel):
    """Audit log entry response."""
    id: str
    user_id: str
    user_name: Optional[str] = None
    user_email: Optional[str] = None
    action: str
    resource_type: str
    resource_id: Optional[str] = None
    details: Optional[Dict[str, Any]] = None
    ip_address: Optional[str] = None
    success: bool
    error_message: Optional[str] = None
    created_at: datetime

    class Config:
        from_attributes = True


class AuditLogFilter(BaseModel):
    """Audit log filter."""
    user_id: Optional[str] = None
    action: Optional[str] = None
    resource_type: Optional[str] = None
    start_date: Optional[datetime] = None
    end_date: Optional[datetime] = None
    success: Optional[bool] = None


class ToolConfigResponse(BaseModel):
    """Tool configuration response."""
    id: str
    tool_name: str
    display_name: str
    description: Optional[str] = None
    is_enabled: bool
    requires_confirmation: bool
    allowed_roles: List[str]
    configuration: Optional[Dict[str, Any]] = None

    class Config:
        from_attributes = True


class ToolConfigUpdate(BaseModel):
    """Tool configuration update."""
    is_enabled: Optional[bool] = None
    requires_confirmation: Optional[bool] = None
    allowed_roles: Optional[List[str]] = None
    configuration: Optional[Dict[str, Any]] = None


class SystemSettingResponse(BaseModel):
    """System setting response."""
    key: str
    value: str
    description: Optional[str] = None
    updated_at: datetime

    class Config:
        from_attributes = True


class SystemSettingUpdate(BaseModel):
    """System setting update."""
    value: str
    description: Optional[str] = None


class ModelConfigResponse(BaseModel):
    """Model configuration response."""
    id: str
    name: str
    deployment_name: str
    description: str
    max_tokens: int
    is_enabled: bool
    is_default: bool
    supports_vision: bool
    supports_tools: bool
