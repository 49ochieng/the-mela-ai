"""Domain enums."""
from __future__ import annotations

from enum import StrEnum


class TaskStatus(StrEnum):
    OPEN = "open"
    IN_PROGRESS = "in_progress"
    DONE = "done"
    IGNORED = "ignored"
    DUPLICATE = "duplicate"
    NEEDS_REVIEW = "needs_review"


class TaskType(StrEnum):
    REVIEW = "review"
    RESPOND = "respond"
    CREATE = "create"
    APPROVE = "approve"
    SCHEDULE = "schedule"
    FORWARD = "forward"
    FOLLOW_UP = "follow_up"
    OTHER = "other"


class Priority(StrEnum):
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"


class SourceType(StrEnum):
    EMAIL = "email"
    TEAMS = "teams"


class ScanType(StrEnum):
    EMAIL = "email"
    TEAMS = "teams"
    ALL = "all"


class ScanStatus(StrEnum):
    # PENDING is kept for backwards-compat; new code should use QUEUED.
    PENDING = "pending"
    QUEUED = "queued"
    RUNNING = "running"
    COMPLETED = "completed"
    COMPLETED_WITH_ERRORS = "completed_with_errors"
    FAILED = "failed"
    CANCELLED = "cancelled"


class ScanStage(StrEnum):
    GRAPH_FETCH = "graph_fetch"
    NORMALIZE = "normalize"
    NOISE_FILTER = "noise_filter"
    DEDUP = "dedup"
    AI_EXTRACT = "ai_extract"
    ATTACHMENT_ARCHIVE = "attachment_archive"
    PERSIST = "persist"
    EXCEL_SYNC = "excel_sync"
    PLANNER_SYNC = "planner_sync"
    CONFIG = "config"


class ScanEventStatus(StrEnum):
    SUCCESS = "success"
    SKIPPED = "skipped"
    NO_TASK = "no_task"
    NEEDS_REVIEW = "needs_review"
    ERROR = "error"


class ConnectionStatus(StrEnum):
    CONNECTED = "connected"
    NEEDS_RECONNECT = "needs_reconnect"
    DISCONNECTED = "disconnected"


class SyncStatus(StrEnum):
    PENDING = "pending"
    SYNCED = "synced"
    SYNC_FAILED = "sync_failed"


class SyncTarget(StrEnum):
    EXCEL = "excel"
    PLANNER = "planner"


class StorageStatus(StrEnum):
    PENDING = "pending"
    ARCHIVED = "archived"
    LINKED = "linked"
    FAILED = "failed"


class UserRole(StrEnum):
    USER = "user"
    ADMIN = "admin"
