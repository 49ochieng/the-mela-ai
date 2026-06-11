"""SQLAlchemy ORM models."""
from __future__ import annotations

import uuid
from datetime import datetime, time
from typing import Optional

from sqlalchemy import (
    JSON,
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
    Time,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .database import Base
from .enums import (
    ConnectionStatus,
    Priority,
    ScanStatus,
    ScanType,
    SourceType,
    StorageStatus,
    SyncStatus,
    SyncTarget,
    TaskStatus,
    TaskType,
    UserRole,
)


def _uuid() -> str:
    return str(uuid.uuid4())


def _now() -> datetime:
    return datetime.utcnow()


class TimestampMixin:
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=_now, onupdate=_now, nullable=False
    )


class Tenant(Base, TimestampMixin):
    __tablename__ = "tenants"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    entra_tenant_id: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    name: Mapped[str] = mapped_column(String(255))


class User(Base, TimestampMixin):
    __tablename__ = "users"
    __table_args__ = (UniqueConstraint("tenant_id", "entra_user_id"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    tenant_id: Mapped[str] = mapped_column(String(36), ForeignKey("tenants.id"), index=True)
    entra_user_id: Mapped[str] = mapped_column(String(64), index=True)
    display_name: Mapped[str] = mapped_column(String(255))
    email: Mapped[str] = mapped_column(String(320), index=True)
    timezone: Mapped[str] = mapped_column(String(64), default="America/Chicago")
    role: Mapped[str] = mapped_column(String(16), default=UserRole.USER.value)
    # GDPR / right-to-erasure: stamped by POST /api/me/delete. The
    # account_deleter worker hard-deletes rows older than the configured
    # grace window. Null = no pending deletion.
    deletion_requested_at: Mapped[Optional[datetime]] = mapped_column(DateTime, index=True)


class GraphConnection(Base, TimestampMixin):
    __tablename__ = "graph_connections"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    tenant_id: Mapped[str] = mapped_column(String(36), index=True)
    user_id: Mapped[str] = mapped_column(String(36), ForeignKey("users.id"), index=True)
    provider: Mapped[str] = mapped_column(String(32), default="microsoft")
    scopes: Mapped[str] = mapped_column(Text, default="")
    status: Mapped[str] = mapped_column(String(32), default=ConnectionStatus.DISCONNECTED.value)
    token_reference: Mapped[Optional[str]] = mapped_column(Text)
    refresh_token_reference: Mapped[Optional[str]] = mapped_column(Text)
    expires_at: Mapped[Optional[datetime]] = mapped_column(DateTime)
    last_connected_at: Mapped[Optional[datetime]] = mapped_column(DateTime)


class ScanSettings(Base, TimestampMixin):
    __tablename__ = "scan_settings"
    __table_args__ = (UniqueConstraint("tenant_id", "user_id"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    tenant_id: Mapped[str] = mapped_column(String(36), index=True)
    user_id: Mapped[str] = mapped_column(String(36), ForeignKey("users.id"), index=True)
    email_scan_enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    teams_scan_enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    daily_scan_enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    scan_time_local: Mapped[time] = mapped_column(Time, default=time(7, 0))
    timezone: Mapped[str] = mapped_column(String(64), default="America/Chicago")
    last_email_scan_at: Mapped[Optional[datetime]] = mapped_column(DateTime)
    last_teams_scan_at: Mapped[Optional[datetime]] = mapped_column(DateTime)
    selected_team_ids: Mapped[list] = mapped_column(JSON, default=list)
    selected_channel_ids: Mapped[list] = mapped_column(JSON, default=list)
    excel_sync_enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    planner_sync_enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    planner_plan_id: Mapped[Optional[str]] = mapped_column(String(128))
    planner_bucket_id: Mapped[Optional[str]] = mapped_column(String(128))
    approval_required_for_planner: Mapped[bool] = mapped_column(Boolean, default=False)
    # Auto-sync after each scan completes
    # one of: none | high | high_medium | all
    auto_sync_to_planner_priority: Mapped[str] = mapped_column(String(16), default="high_medium")
    auto_archive_to_excel: Mapped[bool] = mapped_column(Boolean, default=True)
    mentions_only: Mapped[bool] = mapped_column(Boolean, default=False)
    include_thread_context: Mapped[bool] = mapped_column(Boolean, default=True)
    lookback_hours_first_scan: Mapped[int] = mapped_column(Integer, default=72)
    max_messages_per_scan: Mapped[int] = mapped_column(Integer, default=1000)
    max_ai_calls_per_scan: Mapped[int] = mapped_column(Integer, default=400)
    email_delta_link: Mapped[Optional[str]] = mapped_column(Text)
    email_delta_token: Mapped[Optional[str]] = mapped_column(Text)


class ScanRun(Base, TimestampMixin):
    __tablename__ = "scan_runs"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    tenant_id: Mapped[str] = mapped_column(String(36), index=True)
    user_id: Mapped[str] = mapped_column(String(36), index=True)
    scan_type: Mapped[str] = mapped_column(String(16), default=ScanType.ALL.value)
    source_scope: Mapped[dict] = mapped_column(JSON, default=dict)
    status: Mapped[str] = mapped_column(String(32), default=ScanStatus.PENDING.value)
    started_at: Mapped[Optional[datetime]] = mapped_column(DateTime)
    completed_at: Mapped[Optional[datetime]] = mapped_column(DateTime)
    messages_scanned: Mapped[int] = mapped_column(Integer, default=0)
    messages_skipped: Mapped[int] = mapped_column(Integer, default=0)
    tasks_found: Mapped[int] = mapped_column(Integer, default=0)
    tasks_created: Mapped[int] = mapped_column(Integer, default=0)
    tasks_deduped: Mapped[int] = mapped_column(Integer, default=0)
    errors_count: Mapped[int] = mapped_column(Integer, default=0)
    error_summary: Mapped[Optional[str]] = mapped_column(Text)
    # Per-stage diagnostics — populated by the scan runner so the UI can show
    # *what* happened (not just scanned/tasks/errors).
    noise_skipped_count: Mapped[int] = mapped_column(Integer, default=0)
    duplicate_skipped_count: Mapped[int] = mapped_column(Integer, default=0)
    ai_attempted_count: Mapped[int] = mapped_column(Integer, default=0)
    ai_success_count: Mapped[int] = mapped_column(Integer, default=0)
    ai_no_task_count: Mapped[int] = mapped_column(Integer, default=0)
    ai_failed_count: Mapped[int] = mapped_column(Integer, default=0)
    needs_review_count: Mapped[int] = mapped_column(Integer, default=0)
    attachment_failed_count: Mapped[int] = mapped_column(Integer, default=0)
    excel_failed_count: Mapped[int] = mapped_column(Integer, default=0)
    planner_failed_count: Mapped[int] = mapped_column(Integer, default=0)
    error_categories_json: Mapped[dict] = mapped_column(JSON, default=dict)


class ScanEvent(Base):
    """Per-message diagnostic record. Lets the UI explain *why* a scan ended
    with N errors / 0 tasks without leaking PII (we never store the body)."""
    __tablename__ = "scan_events"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    tenant_id: Mapped[str] = mapped_column(String(36), index=True)
    user_id: Mapped[str] = mapped_column(String(36), index=True)
    scan_run_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("scan_runs.id"), index=True
    )
    source_type: Mapped[Optional[str]] = mapped_column(String(16), nullable=True)
    graph_message_id: Mapped[Optional[str]] = mapped_column(String(512))
    stage: Mapped[str] = mapped_column(String(32), index=True)
    status: Mapped[str] = mapped_column(String(32), index=True)
    category: Mapped[Optional[str]] = mapped_column(String(64))
    message: Mapped[Optional[str]] = mapped_column(Text)
    retryable: Mapped[bool] = mapped_column(Boolean, default=False)
    details_json: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, default=_now, nullable=False, index=True
    )


class SourceMessage(Base, TimestampMixin):
    __tablename__ = "source_messages"
    __table_args__ = (
        UniqueConstraint("tenant_id", "user_id", "source_type", "graph_message_id"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    tenant_id: Mapped[str] = mapped_column(String(36), index=True)
    user_id: Mapped[str] = mapped_column(String(36), index=True)
    source_type: Mapped[str] = mapped_column(String(16))
    graph_message_id: Mapped[str] = mapped_column(String(255), index=True)
    internet_message_id: Mapped[Optional[str]] = mapped_column(String(512), index=True)
    conversation_id: Mapped[Optional[str]] = mapped_column(String(255), index=True)
    reply_to_id: Mapped[Optional[str]] = mapped_column(String(255))
    sender_name: Mapped[Optional[str]] = mapped_column(String(255))
    sender_email: Mapped[Optional[str]] = mapped_column(String(320))
    recipients_json: Mapped[dict] = mapped_column(JSON, default=dict)
    subject_or_channel: Mapped[Optional[str]] = mapped_column(String(1024))
    body_excerpt: Mapped[Optional[str]] = mapped_column(Text)
    body_hash: Mapped[Optional[str]] = mapped_column(String(64), index=True)
    source_link: Mapped[Optional[str]] = mapped_column(Text)
    received_at: Mapped[Optional[datetime]] = mapped_column(DateTime, index=True)
    processed_at: Mapped[Optional[datetime]] = mapped_column(DateTime)
    has_attachments: Mapped[bool] = mapped_column(Boolean, default=False)
    raw_metadata_json: Mapped[dict] = mapped_column(JSON, default=dict)


class Task(Base, TimestampMixin):
    __tablename__ = "tasks"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    tenant_id: Mapped[str] = mapped_column(String(36), index=True)
    user_id: Mapped[str] = mapped_column(String(36), index=True)
    source_message_id: Mapped[Optional[str]] = mapped_column(
        String(36), ForeignKey("source_messages.id"), index=True
    )
    title: Mapped[str] = mapped_column(String(512))
    description: Mapped[Optional[str]] = mapped_column(Text)
    task_type: Mapped[str] = mapped_column(String(32), default=TaskType.OTHER.value)
    assigned_to: Mapped[Optional[str]] = mapped_column(String(320))
    due_date: Mapped[Optional[datetime]] = mapped_column(DateTime, index=True)
    due_date_raw: Mapped[Optional[str]] = mapped_column(String(255))
    priority: Mapped[str] = mapped_column(String(16), default=Priority.MEDIUM.value)
    priority_reasoning: Mapped[Optional[str]] = mapped_column(Text)
    priority_score: Mapped[int] = mapped_column(Integer, default=0, index=True)
    confidence: Mapped[float] = mapped_column(Float, default=0.0)
    evidence: Mapped[Optional[str]] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(32), default=TaskStatus.OPEN.value, index=True)
    source_type: Mapped[str] = mapped_column(String(16), default=SourceType.EMAIL.value)
    source_link: Mapped[Optional[str]] = mapped_column(Text)

    source_message: Mapped[Optional[SourceMessage]] = relationship(lazy="selectin")
    syncs: Mapped[list["TaskSync"]] = relationship(
        back_populates="task", lazy="selectin", cascade="all, delete-orphan"
    )
    attachments: Mapped[list["TaskAttachment"]] = relationship(
        back_populates="task", lazy="selectin", cascade="all, delete-orphan"
    )


class TaskAttachment(Base, TimestampMixin):
    __tablename__ = "task_attachments"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    tenant_id: Mapped[str] = mapped_column(String(36), index=True)
    user_id: Mapped[str] = mapped_column(String(36), index=True)
    task_id: Mapped[str] = mapped_column(String(36), ForeignKey("tasks.id"), index=True)
    source_message_id: Mapped[Optional[str]] = mapped_column(String(36))
    source_attachment_id: Mapped[Optional[str]] = mapped_column(String(255))
    file_name: Mapped[str] = mapped_column(String(512))
    content_type: Mapped[Optional[str]] = mapped_column(String(255))
    size_bytes: Mapped[Optional[int]] = mapped_column(Integer)
    source_url: Mapped[Optional[str]] = mapped_column(Text)
    archive_url: Mapped[Optional[str]] = mapped_column(Text)
    storage_status: Mapped[str] = mapped_column(String(32), default=StorageStatus.PENDING.value)

    task: Mapped[Task] = relationship(back_populates="attachments")


class TaskSync(Base, TimestampMixin):
    __tablename__ = "task_syncs"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    tenant_id: Mapped[str] = mapped_column(String(36), index=True)
    user_id: Mapped[str] = mapped_column(String(36), index=True)
    task_id: Mapped[str] = mapped_column(String(36), ForeignKey("tasks.id"), index=True)
    target_type: Mapped[str] = mapped_column(String(32))
    target_id: Mapped[Optional[str]] = mapped_column(String(255))
    target_url: Mapped[Optional[str]] = mapped_column(Text)
    sync_status: Mapped[str] = mapped_column(String(32), default=SyncStatus.PENDING.value)
    error_message: Mapped[Optional[str]] = mapped_column(Text)
    synced_at: Mapped[Optional[datetime]] = mapped_column(DateTime)

    task: Mapped[Task] = relationship(back_populates="syncs")


class AuditLog(Base):
    __tablename__ = "audit_logs"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    tenant_id: Mapped[str] = mapped_column(String(36), index=True)
    user_id: Mapped[Optional[str]] = mapped_column(String(36), index=True)
    action: Mapped[str] = mapped_column(String(128), index=True)
    entity_type: Mapped[Optional[str]] = mapped_column(String(64))
    entity_id: Mapped[Optional[str]] = mapped_column(String(64))
    details_json: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now, nullable=False)
    # Tamper-evidence: each row's `entry_hash` is sha256(prev_hash || canonical
    # serialisation of this row). `prev_hash` chains it to the immediately
    # preceding row globally (lexicographic id ordering is stable). A verifier
    # can replay the chain end-to-end and any silent edit/delete shows up.
    # `seq` is a per-row monotonic counter used by the verifier to detect
    # missing rows even if the chain is otherwise re-computed correctly.
    prev_hash: Mapped[Optional[str]] = mapped_column(String(64))
    entry_hash: Mapped[Optional[str]] = mapped_column(String(64), index=True)
    seq: Mapped[Optional[int]] = mapped_column(Integer, index=True)
    # Forensic context: source IP and user-agent at time of the action.
    ip: Mapped[Optional[str]] = mapped_column(String(45))
    user_agent: Mapped[Optional[str]] = mapped_column(String(255))
    request_id: Mapped[Optional[str]] = mapped_column(String(36), index=True)


class AgentToken(Base, TimestampMixin):
    """Per-user, revocable bearer token used by Mela / MCP / external agents.

    The plaintext token is shown to the user **once** at creation time and is
    never stored. We persist only the SHA-256 hash. Tokens are bound to a
    specific user + tenant; every Mela/MCP call resolves the calling user
    through this row, so there is no global service key and no header-based
    impersonation. Tokens can be revoked or expired at any time.
    """

    __tablename__ = "agent_tokens"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    tenant_id: Mapped[str] = mapped_column(String(36), index=True)
    user_id: Mapped[str] = mapped_column(String(36), ForeignKey("users.id"), index=True)
    name: Mapped[str] = mapped_column(String(128))
    token_hash: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    scopes_json: Mapped[dict] = mapped_column(JSON, default=dict)
    last_used_at: Mapped[Optional[datetime]] = mapped_column(DateTime)
    expires_at: Mapped[Optional[datetime]] = mapped_column(DateTime)
    revoked_at: Mapped[Optional[datetime]] = mapped_column(DateTime)


class Session(Base, TimestampMixin):
    """Server-side record for every issued JWT session.

    Pairing the JWT's ``jti`` with a DB row lets us revoke individual sessions
    (logout) and "sign out everywhere" by revoking all rows for a user — which
    a stateless JWT alone cannot do. ``ip_hash`` and ``ua_hash`` keep no PII
    while still letting users review active sessions.
    """

    __tablename__ = "sessions"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    tenant_id: Mapped[str] = mapped_column(String(36), index=True)
    user_id: Mapped[str] = mapped_column(String(36), ForeignKey("users.id"), index=True)
    jti: Mapped[str] = mapped_column(String(36), unique=True, index=True)
    issued_at: Mapped[datetime] = mapped_column(DateTime, default=_now)
    last_seen_at: Mapped[datetime] = mapped_column(DateTime, default=_now)
    expires_at: Mapped[datetime] = mapped_column(DateTime)
    ip_hash: Mapped[Optional[str]] = mapped_column(String(64))
    ua_hash: Mapped[Optional[str]] = mapped_column(String(64))
    revoked_at: Mapped[Optional[datetime]] = mapped_column(DateTime)


class OAuthState(Base):
    """One-time, expiring OAuth/PKCE state record.

    Replaces the in-memory ``_state_cache`` dict (which lost state on every
    process restart, leaked memory on abandoned flows, and could not be shared
    across worker processes). The ``flow_json`` blob carries the MSAL
    auth-code-flow dict (state, code_verifier, scopes, redirect_uri, …).
    """

    __tablename__ = "oauth_states"

    state: Mapped[str] = mapped_column(String(64), primary_key=True)
    nonce: Mapped[str] = mapped_column(String(64), index=True)
    flow_json: Mapped[dict] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now)
    expires_at: Mapped[datetime] = mapped_column(DateTime, index=True)
    consumed_at: Mapped[Optional[datetime]] = mapped_column(DateTime)


class TenantConfig(Base, TimestampMixin):
    """Per-tenant Microsoft / integration configuration.

    The actual secret values (client_secret, …) are **never** stored here.
    Only an opaque ``*_secret_ref`` pointer is persisted; the real value lives
    in Azure Key Vault (or, in dev, an env-var-backed store) and is fetched
    on demand by the secret-store layer. This guarantees that no secret is
    ever leaked through the database, audit log, or API response.
    """

    __tablename__ = "tenant_configs"
    __table_args__ = (UniqueConstraint("tenant_id"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    tenant_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("tenants.id"), unique=True, index=True
    )
    # Microsoft Entra
    azure_tenant_id: Mapped[Optional[str]] = mapped_column(String(64))
    azure_client_id: Mapped[Optional[str]] = mapped_column(String(64))
    azure_client_secret_ref: Mapped[Optional[str]] = mapped_column(String(255))
    azure_public_client: Mapped[bool] = mapped_column(Boolean, default=False)
    # Last admin to mutate the config (for audit cross-reference).
    updated_by_user_id: Mapped[Optional[str]] = mapped_column(
        String(36), ForeignKey("users.id")
    )
    last_rotated_at: Mapped[Optional[datetime]] = mapped_column(DateTime)
