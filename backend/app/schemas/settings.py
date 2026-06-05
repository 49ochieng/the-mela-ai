"""
Mela AI - Settings & Connectors Schemas
"""

from pydantic import BaseModel, Field
from typing import Optional, List, Dict, Any
from datetime import datetime, date


# ── User Usage ───────────────────────────────────────────────────────────────

class UserDailyUsage(BaseModel):
    date: date
    conversations: int = 0
    messages: int = 0
    tokens: int = 0
    prompt_tokens: int = 0
    completion_tokens: int = 0
    estimated_cost: float = 0.0


class ModelBreakdown(BaseModel):
    model: str
    request_count: int = 0
    total_tokens: int = 0
    prompt_tokens: int = 0
    completion_tokens: int = 0
    estimated_cost: float = 0.0


class CostBreakdown(BaseModel):
    category: str
    cost: float
    tokens: int
    requests: int


class UserUsageResponse(BaseModel):
    total_conversations: int = 0
    total_messages: int = 0
    tokens_used_today: int = 0
    daily_token_limit: int = 100000
    daily_usage: List[UserDailyUsage] = []
    model_breakdown: List[ModelBreakdown] = []
    total_tokens: int = 0
    total_prompt_tokens: int = 0
    total_completion_tokens: int = 0
    total_requests: int = 0
    estimated_total_cost: float = 0.0
    avg_tokens_per_request: float = 0.0
    avg_cost_per_request: float = 0.0
    cost_by_model: List[CostBreakdown] = []
    peak_hour: int = -1
    token_efficiency_ratio: float = 0.0


# ── User Preferences ────────────────────────────────────────────────────────

class UserPreferences(BaseModel):
    theme: str = "system"  # "light" | "dark" | "system"
    memory_enabled: bool = True
    data_retention_days: int = 365
    default_private_mode: bool = False


# ── Org Settings ─────────────────────────────────────────────────────────────

class OrgSettings(BaseModel):
    """Organization-level settings stored as JSON in SystemSettings key='org_settings'."""
    private_chat_enabled: bool = True
    private_chat_retention_days: int = 20


# ── Features ─────────────────────────────────────────────────────────────────

class UserFeaturesResponse(BaseModel):
    role: str = "user"
    sso_configured: bool = False
    features: Dict[str, bool] = {}


# ── Connectors ───────────────────────────────────────────────────────────────

class ConnectorCreate(BaseModel):
    name: str = Field(..., min_length=1, max_length=200)
    connector_type: str = Field(..., pattern="^(ai_search|sharepoint|onedrive|website|api)$")
    config: Dict[str, Any] = {}
    is_enabled: bool = True


class ConnectorUpdate(BaseModel):
    name: Optional[str] = None
    config: Optional[Dict[str, Any]] = None
    is_enabled: Optional[bool] = None


class ConnectorResponse(BaseModel):
    id: str
    name: str
    connector_type: str
    config: Dict[str, Any] = {}
    is_enabled: bool
    created_at: datetime
    updated_at: datetime

    class Config:
        from_attributes = True
