"""Pydantic schemas for API I/O."""
from __future__ import annotations

from datetime import date, datetime, time
from typing import Any, Optional

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from .enums import (
    ConnectionStatus,
    Priority,
    ScanStatus,
    ScanType,
    SourceType,
    SyncStatus,
    SyncTarget,
    TaskStatus,
    TaskType,
)


class ORM(BaseModel):
    model_config = ConfigDict(from_attributes=True)


# ── Auth / Me ─────────────────────────────────────────────────
class MeResponse(ORM):
    id: str
    tenant_id: str
    entra_user_id: str
    display_name: str
    email: str
    timezone: str
    role: str


# ── Connections ───────────────────────────────────────────────
class ConnectionInfo(ORM):
    provider: str
    status: ConnectionStatus
    scopes: list[str] = []
    last_connected_at: Optional[datetime] = None
    expires_at: Optional[datetime] = None


# ── Settings ──────────────────────────────────────────────────
class ScanSettingsRead(ORM):
    email_scan_enabled: bool
    teams_scan_enabled: bool
    daily_scan_enabled: bool
    scan_time_local: time
    timezone: str
    last_email_scan_at: Optional[datetime]
    last_teams_scan_at: Optional[datetime]
    lookback_hours_first_scan: int
    include_thread_context: bool = False
    max_messages_per_scan: int = 500
    max_ai_calls_per_scan: int = 200


class ScanSettingsUpdate(BaseModel):
    email_scan_enabled: Optional[bool] = None
    teams_scan_enabled: Optional[bool] = None
    daily_scan_enabled: Optional[bool] = None
    scan_time_local: Optional[time] = None
    timezone: Optional[str] = None
    lookback_hours_first_scan: Optional[int] = None
    include_thread_context: Optional[bool] = None
    max_messages_per_scan: Optional[int] = None
    max_ai_calls_per_scan: Optional[int] = None


class TeamsSettingsRead(ORM):
    selected_team_ids: list[str]
    selected_channel_ids: list[str]
    mentions_only: bool
    include_thread_context: bool = False
    teams_scan_enabled: bool = False
    last_teams_scan_at: Optional[datetime] = None


class TeamsSettingsUpdate(BaseModel):
    selected_team_ids: Optional[list[str]] = None
    selected_channel_ids: Optional[list[str]] = None
    mentions_only: Optional[bool] = None
    include_thread_context: Optional[bool] = None
    teams_scan_enabled: Optional[bool] = None


class ExcelSettingsRead(ORM):
    excel_sync_enabled: bool
    auto_archive_to_excel: bool = True


class ExcelSettingsUpdate(BaseModel):
    excel_sync_enabled: Optional[bool] = None
    auto_archive_to_excel: Optional[bool] = None


class PlannerSettingsRead(ORM):
    planner_sync_enabled: bool
    planner_plan_id: Optional[str]
    planner_bucket_id: Optional[str]
    approval_required_for_planner: bool
    auto_sync_to_planner_priority: str = "none"


class PlannerSettingsUpdate(BaseModel):
    planner_sync_enabled: Optional[bool] = None
    planner_plan_id: Optional[str] = None
    planner_bucket_id: Optional[str] = None
    approval_required_for_planner: Optional[bool] = None
    auto_sync_to_planner_priority: Optional[str] = None


# ── Scans ─────────────────────────────────────────────────────
class ScanRunRead(ORM):
    id: str
    scan_type: ScanType
    status: ScanStatus
    started_at: Optional[datetime]
    completed_at: Optional[datetime]
    messages_scanned: int
    messages_skipped: int
    tasks_found: int
    tasks_created: int
    tasks_deduped: int
    errors_count: int
    error_summary: Optional[str]
    # Diagnostics (populated by scan_runner)
    noise_skipped_count: int = 0
    duplicate_skipped_count: int = 0
    ai_attempted_count: int = 0
    ai_success_count: int = 0
    ai_no_task_count: int = 0
    ai_failed_count: int = 0
    needs_review_count: int = 0
    attachment_failed_count: int = 0
    excel_failed_count: int = 0
    planner_failed_count: int = 0
    error_categories: dict[str, int] = Field(default_factory=dict, alias="error_categories_json")

    model_config = ConfigDict(from_attributes=True, populate_by_name=True)


class ScanEventRead(ORM):
    id: str
    scan_run_id: str
    source_type: Optional[str] = None
    graph_message_id: Optional[str]
    stage: str
    status: str
    category: Optional[str]
    message: Optional[str]
    retryable: bool
    created_at: datetime


class RunScanRequest(BaseModel):
    source: ScanType = ScanType.ALL
    lookback_hours: Optional[int] = None
    include_attachments: bool = True
    wait_for_completion: bool = False


class RunScanResponse(BaseModel):
    scan_run_id: str
    status: ScanStatus


# ── Tasks ─────────────────────────────────────────────────────
class TaskSyncRead(ORM):
    target_type: SyncTarget
    target_url: Optional[str]
    sync_status: SyncStatus
    synced_at: Optional[datetime]
    error_message: Optional[str]


class TaskRead(ORM):
    id: str
    title: str
    description: Optional[str]
    task_type: TaskType
    assigned_to: Optional[str]
    due_date: Optional[datetime]
    due_date_raw: Optional[str]
    priority: Priority
    priority_reasoning: Optional[str]
    priority_score: int = 0
    confidence: float
    evidence: Optional[str]
    status: TaskStatus
    source_type: SourceType
    source_link: Optional[str]
    created_at: datetime
    syncs: list[TaskSyncRead] = []
    source_meta: dict = Field(default_factory=dict)

    @model_validator(mode="before")
    @classmethod
    def _hoist_source_meta(cls, obj):  # type: ignore[no-untyped-def]
        # Build source_meta from the related SourceMessage when present so the
        # web UI can render Teams team/channel/sender details without an
        # additional round-trip.
        sm = getattr(obj, "source_message", None)
        if sm is None:
            return obj
        meta = {
            "source_type": getattr(sm, "source_type", None),
            "subject_or_channel": getattr(sm, "subject_or_channel", None),
            "sender_name": getattr(sm, "sender_name", None),
            "sender_email": getattr(sm, "sender_email", None),
            "received_at": getattr(sm, "received_at", None),
            "source_link": getattr(sm, "source_link", None),
            **(getattr(sm, "raw_metadata_json", None) or {}),
        }
        try:
            setattr(obj, "source_meta", meta)
        except Exception:
            pass
        return obj


class TaskUpdate(BaseModel):
    title: Optional[str] = None
    description: Optional[str] = None
    due_date: Optional[datetime] = None
    priority: Optional[Priority] = None
    status: Optional[TaskStatus] = None


class TaskListResponse(BaseModel):
    items: list[TaskRead]
    total: int


# ── Excel / Planner ──────────────────────────────────────────
class ExcelStatus(BaseModel):
    workbook_id: Optional[str] = None
    workbook_url: Optional[str] = None
    last_sync_at: Optional[datetime] = None
    last_error: Optional[str] = None


class ExcelSyncRequest(BaseModel):
    task_ids: Optional[list[str]] = None


class ExcelSyncResponse(BaseModel):
    synced: int
    failed: int
    workbook_url: Optional[str]


class PlannerPlan(BaseModel):
    id: str
    title: str
    group_id: Optional[str] = None


class PlannerBucket(BaseModel):
    id: str
    name: str


class CreatePlannerTasksRequest(BaseModel):
    task_ids: list[str]
    plan_id: Optional[str] = None
    bucket_id: Optional[str] = None


# ── AI extraction (strict JSON schema) ────────────────────────
class ExtractedTask(BaseModel):
    title: str = Field(min_length=1, max_length=512)
    description: str
    task_type: TaskType
    assigned_to: Optional[str] = None
    due_date: Optional[str] = None  # ISO 8601 date
    due_date_raw: Optional[str] = None
    priority: Priority
    priority_reasoning: str
    confidence: float = Field(ge=0.0, le=1.0)
    evidence: str

    @field_validator("due_date")
    @classmethod
    def _validate_due_date(cls, v: Optional[str]) -> Optional[str]:
        if v is None or v == "":
            return None
        # Accept ISO date or datetime; let downstream parser convert.
        return v


class ExtractionResult(BaseModel):
    has_task: bool
    tasks: list[ExtractedTask] = []
