"""
Mela AI - Database Models
"""

from datetime import datetime
from typing import Optional, List
from sqlalchemy import (
    String, Integer, DateTime, Boolean, Text, Float,
    ForeignKey, JSON, Enum, Index, UniqueConstraint
)
from sqlalchemy.orm import relationship, Mapped, mapped_column
# UNIQUEIDENTIFIER is only used with MSSQL; omit import for cross-DB compat
import uuid
import enum

from app.core.database import Base


class UserRole(str, enum.Enum):
    """Role tiers — extended to 6-tier in Sprint 3.1.

    Migration mapping (executed in 005_expand_user_roles.py):
      admin   → platform_admin   (no tenant scope) OR tenant_admin (scoped)
      user    → standard_user
      viewer  → read_only_user

    The legacy values (admin/user/viewer) remain valid identifiers so any
    code that compares string literals (== "admin") doesn't break during
    the rollout window. New code should prefer the new identifiers.
    """
    # Legacy values — kept for backward compatibility during the migration.
    ADMIN = "admin"
    USER = "user"
    VIEWER = "viewer"
    # New 6-tier roles.
    PLATFORM_ADMIN = "platform_admin"
    TENANT_ADMIN = "tenant_admin"
    POWER_USER = "power_user"
    STANDARD_USER = "standard_user"
    READ_ONLY_USER = "read_only_user"
    SERVICE_ACCOUNT = "service_account"


# Aggregate sets used by authorization helpers. Defined at module scope so
# they can be imported without circular dependency risks.
ADMIN_ROLES = {UserRole.ADMIN, UserRole.PLATFORM_ADMIN, UserRole.TENANT_ADMIN}
GLOBAL_ADMIN_ROLES = {UserRole.ADMIN, UserRole.PLATFORM_ADMIN}
HUMAN_ROLES = {
    UserRole.ADMIN, UserRole.PLATFORM_ADMIN, UserRole.TENANT_ADMIN,
    UserRole.POWER_USER, UserRole.USER, UserRole.STANDARD_USER,
    UserRole.VIEWER, UserRole.READ_ONLY_USER,
}


def role_is_admin(role) -> bool:
    """Backward-compatible admin check. Accepts UserRole or raw string."""
    if isinstance(role, UserRole):
        return role in ADMIN_ROLES
    return str(role).lower() in {r.value for r in ADMIN_ROLES}


def role_is_global_admin(role) -> bool:
    """Platform-wide admin (not tenant-scoped)."""
    if isinstance(role, UserRole):
        return role in GLOBAL_ADMIN_ROLES
    return str(role).lower() in {r.value for r in GLOBAL_ADMIN_ROLES}


class ModelType(str, enum.Enum):
    CHAT = "chat"
    FAST = "fast"
    VISION = "vision"
    EMBEDDING = "embedding"


class ToolType(str, enum.Enum):
    EMAIL = "email"
    CALENDAR = "calendar"
    TEAMS = "teams"
    PLANNER = "planner"
    SHAREPOINT = "sharepoint"


class User(Base):
    """User model."""
    __tablename__ = "users"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    azure_id: Mapped[str] = mapped_column(String(36), unique=True, nullable=False)
    email: Mapped[str] = mapped_column(String(255), unique=True, nullable=False)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    department: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    job_title: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    role: Mapped[UserRole] = mapped_column(Enum(UserRole), default=UserRole.USER)
    preferred_model: Mapped[Optional[str]] = mapped_column(String(50), nullable=True)
    daily_token_limit: Mapped[int] = mapped_column(Integer, default=100000)
    tokens_used_today: Mapped[int] = mapped_column(Integer, default=0)
    last_token_reset: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    # Set once when the user is auto-elevated via BOOTSTRAP_ADMIN_EMAILS.
    # NULL means the user was never bootstrap-elevated (normal path).
    bootstrap_elevated_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    # Set when an admin manually promotes this user to admin via PUT /admin/users/{id}.
    # Used to show the "You've been promoted" banner on their next login.
    promoted_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    # Flipped to True after the promoted user dismisses the banner (ack endpoint).
    promotion_banner_shown: Mapped[bool] = mapped_column(Boolean, default=False)
    # GDPR Sprint 2: soft-delete marker. NULL = active; set = pending hard-delete
    # after the retention window. Anonymisation of email/name happens at erasure
    # time; this timestamp drives the retention sweep.
    deleted_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    # Relationships
    conversations: Mapped[List["Conversation"]] = relationship("Conversation", back_populates="user", cascade="all, delete-orphan")
    documents: Mapped[List["Document"]] = relationship("Document", back_populates="uploaded_by_user")
    audit_logs: Mapped[List["AuditLog"]] = relationship("AuditLog", back_populates="user")

    __table_args__ = (
        Index("ix_users_azure_id", "azure_id"),
        Index("ix_users_email", "email"),
    )


class Conversation(Base):
    """Conversation model."""
    __tablename__ = "conversations"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    user_id: Mapped[str] = mapped_column(String(36), ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    title: Mapped[str] = mapped_column(String(255), nullable=False, default="New Conversation")
    model: Mapped[str] = mapped_column(String(50), nullable=False, default="gpt-5.2-chat")
    system_prompt: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    context_document_ids: Mapped[Optional[List[str]]] = mapped_column(JSON, nullable=True)
    is_archived: Mapped[bool] = mapped_column(Boolean, default=False)
    is_private: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    private_expires_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    project_id: Mapped[Optional[str]] = mapped_column(String(36), ForeignKey("projects.id"), nullable=True)
    # Profile / context separation  ── AUTHORITATIVE fields ──────────────────
    # profile_mode is the canonical namespace field ('work' | 'personal').
    # context_type is kept for backward compatibility (maps 'org' → 'work').
    profile_mode: Mapped[str] = mapped_column(String(20), nullable=False, default="personal")  # 'work' | 'personal'
    tenant_id: Mapped[Optional[str]] = mapped_column(String(36), nullable=True)  # required for work, NULL for personal
    context_type: Mapped[str] = mapped_column(String(20), nullable=False, default="personal")  # legacy alias
    workspace_id: Mapped[Optional[str]] = mapped_column(String(36), nullable=True)
    # GDPR Sprint 2: soft-delete marker.
    deleted_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    # Relationships
    user: Mapped["User"] = relationship("User", back_populates="conversations")
    messages: Mapped[List["Message"]] = relationship("Message", back_populates="conversation", cascade="all, delete-orphan", order_by="Message.created_at")
    project: Mapped[Optional["Project"]] = relationship("Project", back_populates="conversations")
    members: Mapped[List["ChatMember"]] = relationship("ChatMember", back_populates="conversation", cascade="all, delete-orphan")

    __table_args__ = (
        Index("ix_conversations_user_id", "user_id"),
        Index("ix_conversations_created_at", "created_at"),
        Index("ix_conversations_private_expires", "is_private", "private_expires_at"),
        Index("ix_conversations_profile_mode", "user_id", "profile_mode", "tenant_id", "updated_at"),
        Index("ix_conversations_context_type", "context_type"),
        Index("ix_conversations_workspace_id", "workspace_id"),
    )


class Message(Base):
    """Message model."""
    __tablename__ = "messages"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    conversation_id: Mapped[str] = mapped_column(String(36), ForeignKey("conversations.id", ondelete="CASCADE"), nullable=False)
    role: Mapped[str] = mapped_column(String(20), nullable=False)  # user, assistant, system, tool
    content: Mapped[str] = mapped_column(Text, nullable=False)
    tokens_used: Mapped[int] = mapped_column(Integer, default=0)
    model: Mapped[Optional[str]] = mapped_column(String(50), nullable=True)
    tool_calls: Mapped[Optional[dict]] = mapped_column(JSON, nullable=True)
    tool_results: Mapped[Optional[dict]] = mapped_column(JSON, nullable=True)
    citations: Mapped[Optional[List[dict]]] = mapped_column(JSON, nullable=True)
    attachments: Mapped[Optional[List[dict]]] = mapped_column(JSON, nullable=True)
    # Profile namespace — mirrored from the parent conversation for integrity checks
    profile_mode: Mapped[str] = mapped_column(String(20), nullable=False, default="personal")
    tenant_id: Mapped[Optional[str]] = mapped_column(String(36), nullable=True)
    # GDPR Sprint 2: soft-delete marker.
    deleted_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    # Relationships
    conversation: Mapped["Conversation"] = relationship("Conversation", back_populates="messages")

    __table_args__ = (
        Index("ix_messages_conversation_id", "conversation_id"),
        Index("ix_messages_created_at", "created_at"),
        Index("ix_messages_profile_mode", "profile_mode", "tenant_id"),
    )


class Document(Base):
    """Document model for RAG pipeline."""
    __tablename__ = "documents"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    title: Mapped[str] = mapped_column(String(500), nullable=False)
    filename: Mapped[str] = mapped_column(String(500), nullable=False)
    file_type: Mapped[str] = mapped_column(String(50), nullable=False)
    file_size: Mapped[int] = mapped_column(Integer, nullable=False)
    blob_url: Mapped[str] = mapped_column(String(1000), nullable=False)
    source: Mapped[str] = mapped_column(String(50), nullable=False)  # upload, sharepoint, web
    source_url: Mapped[Optional[str]] = mapped_column(String(1000), nullable=True)
    content_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    chunk_count: Mapped[int] = mapped_column(Integer, default=0)
    is_indexed: Mapped[bool] = mapped_column(Boolean, default=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    doc_metadata: Mapped[Optional[dict]] = mapped_column(JSON, nullable=True)
    uploaded_by: Mapped[str] = mapped_column(String(36), ForeignKey("users.id"), nullable=False)
    # GDPR Sprint 2: soft-delete marker.
    deleted_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    # Relationships
    uploaded_by_user: Mapped["User"] = relationship("User", back_populates="documents")
    chunks: Mapped[List["DocumentChunk"]] = relationship("DocumentChunk", back_populates="document", cascade="all, delete-orphan")

    __table_args__ = (
        Index("ix_documents_uploaded_by", "uploaded_by"),
        Index("ix_documents_content_hash", "content_hash"),
    )


class DocumentChunk(Base):
    """Document chunk model for embeddings."""
    __tablename__ = "document_chunks"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    document_id: Mapped[str] = mapped_column(String(36), ForeignKey("documents.id", ondelete="CASCADE"), nullable=False)
    chunk_index: Mapped[int] = mapped_column(Integer, nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    token_count: Mapped[int] = mapped_column(Integer, nullable=False)
    search_index_id: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
    doc_metadata: Mapped[Optional[dict]] = mapped_column(JSON, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    # Relationships
    document: Mapped["Document"] = relationship("Document", back_populates="chunks")

    __table_args__ = (
        Index("ix_document_chunks_document_id", "document_id"),
    )


class AuditLog(Base):
    """Audit log model."""
    __tablename__ = "audit_logs"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    user_id: Mapped[str] = mapped_column(String(36), ForeignKey("users.id"), nullable=False)
    action: Mapped[str] = mapped_column(String(100), nullable=False)
    event_type: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)  # collaboration-specific event
    resource_type: Mapped[str] = mapped_column(String(50), nullable=False)
    resource_id: Mapped[Optional[str]] = mapped_column(String(36), nullable=True)
    workspace_id: Mapped[Optional[str]] = mapped_column(String(36), nullable=True)  # org workspace context
    details: Mapped[Optional[dict]] = mapped_column(JSON, nullable=True)
    ip_address: Mapped[Optional[str]] = mapped_column(String(45), nullable=True)
    user_agent: Mapped[Optional[str]] = mapped_column(String(500), nullable=True)
    success: Mapped[bool] = mapped_column(Boolean, default=True)
    error_message: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    # Relationships
    user: Mapped["User"] = relationship("User", back_populates="audit_logs")

    __table_args__ = (
        Index("ix_audit_logs_user_id", "user_id"),
        Index("ix_audit_logs_action", "action"),
        Index("ix_audit_logs_created_at", "created_at"),
        Index("ix_audit_logs_workspace_id", "workspace_id"),
    )


class UserSession(Base):
    """Server-side session record for token revocation and idle/absolute lifetime enforcement.

    A row is created when a user authenticates. The token's `jti` (or a synthetic
    identifier for legacy paths) links the access token to this row so that:
      - logout can revoke the session (`revoked_at` set)
      - admin disable can revoke all of a user's sessions
      - middleware can enforce idle timeout (last_activity_at) and absolute expiry
    """
    __tablename__ = "user_sessions"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    user_id: Mapped[str] = mapped_column(String(36), ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    token_jti: Mapped[str] = mapped_column(String(128), nullable=False)
    issued_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    last_activity_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, nullable=False)
    revoked_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    ip_address: Mapped[Optional[str]] = mapped_column(String(45), nullable=True)
    user_agent: Mapped[Optional[str]] = mapped_column(String(500), nullable=True)

    __table_args__ = (
        Index("ix_user_sessions_user_id", "user_id"),
        Index("ix_user_sessions_token_jti", "token_jti"),
        UniqueConstraint("token_jti", name="uq_user_sessions_token_jti"),
    )


class GeneratedFileLog(Base):
    """Audit record for every file produced by the code interpreter."""
    __tablename__ = "generated_file_logs"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    message_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("messages.id", ondelete="CASCADE"), nullable=False
    )
    conversation_id: Mapped[str] = mapped_column(String(36), nullable=False)
    user_id: Mapped[str] = mapped_column(String(36), nullable=False)
    filename: Mapped[str] = mapped_column(String(500), nullable=False)
    mime_type: Mapped[str] = mapped_column(String(200), nullable=False)
    file_size: Mapped[int] = mapped_column(Integer, nullable=False)
    # JSON list of input file names that were passed to the sandbox
    source_inputs: Mapped[Optional[list]] = mapped_column(JSON, nullable=True)
    # 'excel' | 'pdf' | 'word' | 'image' | 'csv' | 'zip' | 'other'
    output_type: Mapped[str] = mapped_column(String(50), nullable=False, default="other")
    # Base64-encoded file content — stored so the file can be re-downloaded
    # from history without re-running the code.  NULL for very large files
    # (> 10 MB) or when generation predates this column.
    file_data: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    __table_args__ = (
        Index("ix_gen_file_logs_message_id", "message_id"),
        Index("ix_gen_file_logs_user_id", "user_id"),
        Index("ix_gen_file_logs_created_at", "created_at"),
    )


class ModelUsage(Base):
    """Model usage tracking."""
    __tablename__ = "model_usage"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    user_id: Mapped[str] = mapped_column(String(36), ForeignKey("users.id"), nullable=False)
    model: Mapped[str] = mapped_column(String(50), nullable=False)
    prompt_tokens: Mapped[int] = mapped_column(Integer, default=0)
    completion_tokens: Mapped[int] = mapped_column(Integer, default=0)
    total_tokens: Mapped[int] = mapped_column(Integer, default=0)
    conversation_id: Mapped[Optional[str]] = mapped_column(String(36), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    __table_args__ = (
        Index("ix_model_usage_user_id", "user_id"),
        Index("ix_model_usage_created_at", "created_at"),
    )


class SystemSettings(Base):
    """System settings model."""
    __tablename__ = "system_settings"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    key: Mapped[str] = mapped_column(String(100), unique=True, nullable=False)
    value: Mapped[str] = mapped_column(Text, nullable=False)
    description: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    updated_by: Mapped[Optional[str]] = mapped_column(String(36), nullable=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


class Project(Base):
    """Project workspace model."""
    __tablename__ = "projects"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    user_id: Mapped[str] = mapped_column(String(36), ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    description: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    icon: Mapped[Optional[str]] = mapped_column(String(10), nullable=True)
    color: Mapped[Optional[str]] = mapped_column(String(20), nullable=True)
    system_prompt: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    is_archived: Mapped[bool] = mapped_column(Boolean, default=False)
    # Profile / context separation  ── AUTHORITATIVE fields ──────────────────
    profile_mode: Mapped[str] = mapped_column(String(20), nullable=False, default="personal")  # 'work' | 'personal'
    tenant_id: Mapped[Optional[str]] = mapped_column(String(36), nullable=True)
    context_type: Mapped[str] = mapped_column(String(20), nullable=False, default="personal")  # legacy alias
    workspace_id: Mapped[Optional[str]] = mapped_column(String(36), nullable=True)
    # GDPR Sprint 2: soft-delete marker.
    deleted_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    updated_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)

    # Relationships
    conversations: Mapped[List["Conversation"]] = relationship("Conversation", back_populates="project")
    memories: Mapped[List["ProjectMemory"]] = relationship("ProjectMemory", back_populates="project", cascade="all, delete-orphan")
    files: Mapped[List["ProjectFile"]] = relationship("ProjectFile", back_populates="project", cascade="all, delete-orphan")
    members: Mapped[List["ProjectMember"]] = relationship("ProjectMember", back_populates="project", cascade="all, delete-orphan")

    __table_args__ = (
        Index("ix_projects_user_id", "user_id"),
        Index("ix_projects_profile_mode", "user_id", "profile_mode", "tenant_id", "updated_at"),
        Index("ix_projects_context_type", "context_type"),
        Index("ix_projects_workspace_id", "workspace_id"),
    )


class ProjectMemory(Base):
    """Auto-extracted memory facts per project."""
    __tablename__ = "project_memories"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    project_id: Mapped[str] = mapped_column(String(36), ForeignKey("projects.id", ondelete="CASCADE"), nullable=False)
    fact: Mapped[str] = mapped_column(Text, nullable=False)
    source_conversation_id: Mapped[Optional[str]] = mapped_column(String(36), ForeignKey("conversations.id", ondelete="SET NULL"), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    # Relationships
    project: Mapped["Project"] = relationship("Project", back_populates="memories")

    __table_args__ = (
        Index("ix_project_memories_project_id", "project_id"),
        Index("ix_project_memories_created_at", "created_at"),
    )


class ProjectFile(Base):
    """File attached to a project workspace."""
    __tablename__ = "project_files"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    project_id: Mapped[str] = mapped_column(String(36), ForeignKey("projects.id", ondelete="CASCADE"), nullable=False)
    filename: Mapped[str] = mapped_column(String(500), nullable=False)
    file_type: Mapped[str] = mapped_column(String(100), nullable=False)
    file_size: Mapped[int] = mapped_column(Integer, nullable=False)
    content_text: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    uploaded_by: Mapped[str] = mapped_column(String(36), ForeignKey("users.id"), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    # Relationships
    project: Mapped["Project"] = relationship("Project", back_populates="files")

    __table_args__ = (
        Index("ix_project_files_project_id", "project_id"),
    )


class ConnectorType(str, enum.Enum):
    AI_SEARCH = "ai_search"
    SHAREPOINT = "sharepoint"
    ONEDRIVE = "onedrive"
    WEBSITE = "website"
    API = "api"


class Connector(Base):
    """User data-source connector."""
    __tablename__ = "connectors"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    connector_type: Mapped[ConnectorType] = mapped_column(Enum(ConnectorType), nullable=False)
    config: Mapped[Optional[dict]] = mapped_column(JSON, nullable=True)
    is_enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    user_id: Mapped[str] = mapped_column(String(36), ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    __table_args__ = (
        Index("ix_connectors_user_id", "user_id"),
    )


class EnabledTool(Base):
    """Enabled tools configuration."""
    __tablename__ = "enabled_tools"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    tool_name: Mapped[str] = mapped_column(String(50), unique=True, nullable=False)
    display_name: Mapped[str] = mapped_column(String(100), nullable=False)
    description: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    is_enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    requires_confirmation: Mapped[bool] = mapped_column(Boolean, default=True)
    allowed_roles: Mapped[List[str]] = mapped_column(JSON, default=["admin", "user"])
    configuration: Mapped[Optional[dict]] = mapped_column(JSON, nullable=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


# ─────────────────────────────────────────────────────────────────────────────
# Collaboration models
# ─────────────────────────────────────────────────────────────────────────────

class MemberRole(str, enum.Enum):
    OWNER = "owner"
    EDITOR = "editor"
    VIEWER = "viewer"


class ProjectMember(Base):
    """Members of a shared project workspace."""
    __tablename__ = "project_members"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    project_id: Mapped[str] = mapped_column(String(36), ForeignKey("projects.id", ondelete="CASCADE"), nullable=False)
    user_id: Mapped[str] = mapped_column(String(36), ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    role: Mapped[MemberRole] = mapped_column(Enum(MemberRole), nullable=False, default=MemberRole.VIEWER)
    added_by: Mapped[str] = mapped_column(String(36), ForeignKey("users.id"), nullable=False)
    added_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    # Relationships
    project: Mapped["Project"] = relationship("Project", back_populates="members")
    user: Mapped["User"] = relationship("User", foreign_keys=[user_id])
    added_by_user: Mapped["User"] = relationship("User", foreign_keys=[added_by])

    __table_args__ = (
        UniqueConstraint("project_id", "user_id", name="uq_project_members_project_user"),
        Index("ix_project_members_project_id", "project_id"),
        Index("ix_project_members_user_id", "user_id"),
    )


class ChatMember(Base):
    """Members of a shared standard chat conversation."""
    __tablename__ = "chat_members"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    conversation_id: Mapped[str] = mapped_column(String(36), ForeignKey("conversations.id", ondelete="CASCADE"), nullable=False)
    user_id: Mapped[str] = mapped_column(String(36), ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    role: Mapped[MemberRole] = mapped_column(Enum(MemberRole), nullable=False, default=MemberRole.VIEWER)
    added_by: Mapped[str] = mapped_column(String(36), ForeignKey("users.id"), nullable=False)
    added_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    # Relationships
    conversation: Mapped["Conversation"] = relationship("Conversation", back_populates="members")
    user: Mapped["User"] = relationship("User", foreign_keys=[user_id])
    added_by_user: Mapped["User"] = relationship("User", foreign_keys=[added_by])

    __table_args__ = (
        UniqueConstraint("conversation_id", "user_id", name="uq_chat_members_conv_user"),
        Index("ix_chat_members_conversation_id", "conversation_id"),
        Index("ix_chat_members_user_id", "user_id"),
    )


class InviteStatus(str, enum.Enum):
    PENDING = "pending"
    ACCEPTED = "accepted"
    REVOKED = "revoked"
    EXPIRED = "expired"


class Invite(Base):
    """Pending / accepted invitations to a project or chat."""
    __tablename__ = "invites"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    resource_type: Mapped[str] = mapped_column(String(20), nullable=False)  # 'project' | 'chat'
    resource_id: Mapped[str] = mapped_column(String(36), nullable=False)
    inviter_user_id: Mapped[str] = mapped_column(String(36), ForeignKey("users.id"), nullable=False)
    invitee_email: Mapped[str] = mapped_column(String(255), nullable=False)
    invitee_user_id: Mapped[Optional[str]] = mapped_column(String(36), ForeignKey("users.id"), nullable=True)
    role: Mapped[MemberRole] = mapped_column(Enum(MemberRole), nullable=False, default=MemberRole.VIEWER)
    status: Mapped[InviteStatus] = mapped_column(Enum(InviteStatus), nullable=False, default=InviteStatus.PENDING)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    expires_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)

    # Relationships
    inviter: Mapped["User"] = relationship("User", foreign_keys=[inviter_user_id])
    invitee_user: Mapped[Optional["User"]] = relationship("User", foreign_keys=[invitee_user_id])

    __table_args__ = (
        Index("ix_invites_resource", "resource_type", "resource_id"),
        Index("ix_invites_invitee_email", "invitee_email"),
        Index("ix_invites_status", "status"),
    )


class ShareLink(Base):
    """Optional share links (disabled by default, policy-controlled)."""
    __tablename__ = "share_links"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    resource_type: Mapped[str] = mapped_column(String(20), nullable=False)  # 'project' | 'chat'
    resource_id: Mapped[str] = mapped_column(String(36), nullable=False)
    created_by: Mapped[str] = mapped_column(String(36), ForeignKey("users.id"), nullable=False)
    permission_scope: Mapped[MemberRole] = mapped_column(Enum(MemberRole), nullable=False, default=MemberRole.VIEWER)
    expires_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    revoked_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    # Relationships
    creator: Mapped["User"] = relationship("User", foreign_keys=[created_by])

    __table_args__ = (
        Index("ix_share_links_resource", "resource_type", "resource_id"),
        Index("ix_share_links_created_by", "created_by"),
    )


class ModelRanking(Base):
    """Admin-configurable model ranking / routing preferences."""
    __tablename__ = "model_rankings"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    model_id: Mapped[str] = mapped_column(String(100), nullable=False, unique=True)
    display_name: Mapped[str] = mapped_column(String(100), nullable=False)
    provider: Mapped[str] = mapped_column(String(50), nullable=False, default="azure_openai")
    rank: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    is_enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    is_default: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    max_tokens: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    notes: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    # Cost multiplier shown to users next to the model attribution. Admin-editable.
    # 1.0 = baseline (cheapest tier). Defaults populated by seed.
    cost_multiplier: Mapped[float] = mapped_column(Float, nullable=False, default=1.0, server_default="1.0")
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    updated_by: Mapped[Optional[str]] = mapped_column(String(36), nullable=True)

    __table_args__ = (
        Index("ix_model_rankings_rank", "rank"),
        Index("ix_model_rankings_provider", "provider"),
    )


class SystemInstruction(Base):
    """Layered system instructions injected into the AI context."""
    __tablename__ = "system_instructions"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    # scope: global > org > team > user
    scope: Mapped[str] = mapped_column(String(20), nullable=False, default="user")
    priority: Mapped[int] = mapped_column(Integer, nullable=False, default=100)
    is_enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    created_by: Mapped[str] = mapped_column(String(36), nullable=False)
    user_id: Mapped[Optional[str]] = mapped_column(String(36), nullable=True)
    tenant_id: Mapped[Optional[str]] = mapped_column(String(36), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    __table_args__ = (
        Index("ix_instructions_scope_priority", "scope", "priority"),
        Index("ix_instructions_user_id", "user_id"),
    )


class Skill(Base):
    """AI skills — instruction blocks triggered by category or keywords."""
    __tablename__ = "skills"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=True)
    category: Mapped[str] = mapped_column(String(50), nullable=False, default="general")
    # JSON list of trigger keywords
    trigger_keywords: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    instruction_block: Mapped[str] = mapped_column(Text, nullable=False)
    model_preference: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
    is_enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    is_builtin: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    rank: Mapped[int] = mapped_column(Integer, nullable=False, default=100)
    # visibility: global (all users), org, user
    visibility: Mapped[str] = mapped_column(String(20), nullable=False, default="global")
    created_by: Mapped[str] = mapped_column(String(36), nullable=False)
    user_id: Mapped[Optional[str]] = mapped_column(String(36), nullable=True)
    tenant_id: Mapped[Optional[str]] = mapped_column(String(36), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    __table_args__ = (
        Index("ix_skills_category", "category"),
        Index("ix_skills_rank", "rank"),
        Index("ix_skills_visibility", "visibility"),
    )


class ClaudeUsage(Base):
    """Tracks per-user Claude question count per daily window."""
    __tablename__ = "claude_usage"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    user_id: Mapped[str] = mapped_column(String(36), nullable=False)
    window_date: Mapped[str] = mapped_column(String(10), nullable=False)  # YYYY-MM-DD UTC
    question_count: Mapped[int] = mapped_column(Integer, default=0)
    token_count: Mapped[int] = mapped_column(Integer, default=0)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    __table_args__ = (
        UniqueConstraint("user_id", "window_date", name="uq_claude_usage_user_day"),
        Index("ix_claude_usage_user_date", "user_id", "window_date"),
    )


# ─────────────────────────────────────────────────────────────────────────────
# Workflow automation models
# ─────────────────────────────────────────────────────────────────────────────

class WorkflowStatus(str, enum.Enum):
    ACTIVE = "active"
    PAUSED = "paused"
    DRAFT = "draft"
    ARCHIVED = "archived"


class WorkflowRunStatus(str, enum.Enum):
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    SKIPPED = "skipped"


class Workflow(Base):
    """Automation workflow — trigger + action pipeline definition."""
    __tablename__ = "workflows"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    description: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    # Trigger: schedule | keyword | event | manual
    trigger_type: Mapped[str] = mapped_column(String(50), nullable=False, default="manual")
    trigger_config: Mapped[Optional[dict]] = mapped_column(JSON, nullable=True)
    # Actions: list of {type, config} dicts
    actions: Mapped[Optional[list]] = mapped_column(JSON, nullable=True)
    status: Mapped[WorkflowStatus] = mapped_column(Enum(WorkflowStatus), nullable=False, default=WorkflowStatus.DRAFT)
    # Scope: global (all users), org (tenant), user
    visibility: Mapped[str] = mapped_column(String(20), nullable=False, default="user")
    created_by: Mapped[str] = mapped_column(String(36), ForeignKey("users.id"), nullable=False)
    user_id: Mapped[Optional[str]] = mapped_column(String(36), nullable=True)
    tenant_id: Mapped[Optional[str]] = mapped_column(String(36), nullable=True)
    run_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    last_run_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    # Relationships
    creator: Mapped["User"] = relationship("User", foreign_keys=[created_by])
    runs: Mapped[List["WorkflowRun"]] = relationship("WorkflowRun", back_populates="workflow", cascade="all, delete-orphan")

    __table_args__ = (
        Index("ix_workflows_created_by", "created_by"),
        Index("ix_workflows_status", "status"),
        Index("ix_workflows_trigger_type", "trigger_type"),
    )


class WorkflowRun(Base):
    """Execution record for a single workflow invocation."""
    __tablename__ = "workflow_runs"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    workflow_id: Mapped[str] = mapped_column(String(36), ForeignKey("workflows.id", ondelete="CASCADE"), nullable=False)
    triggered_by: Mapped[Optional[str]] = mapped_column(String(36), nullable=True)  # user_id or 'system'
    trigger_type: Mapped[str] = mapped_column(String(50), nullable=False, default="manual")
    status: Mapped[WorkflowRunStatus] = mapped_column(Enum(WorkflowRunStatus), nullable=False, default=WorkflowRunStatus.PENDING)
    input_data: Mapped[Optional[dict]] = mapped_column(JSON, nullable=True)
    output_data: Mapped[Optional[dict]] = mapped_column(JSON, nullable=True)
    error_message: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    steps_completed: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    steps_total: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    started_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    finished_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    # Phase 5B: trace IDs spawned by 'orchestrate' actions during this
    # run.  Lets admins correlate workflow runs to orchestration traces
    # in the trace viewer.  Empty list when the workflow used no
    # orchestration actions.
    orchestration_trace_ids: Mapped[Optional[list]] = mapped_column(
        JSON, nullable=True, default=list,
    )

    # Relationships
    workflow: Mapped["Workflow"] = relationship("Workflow", back_populates="runs")

    __table_args__ = (
        Index("ix_workflow_runs_workflow_id", "workflow_id"),
        Index("ix_workflow_runs_status", "status"),
        Index("ix_workflow_runs_created_at", "created_at"),
    )


# ─────────────────────────────────────────────────────────────────────────────
# Enterprise control-plane models
# ─────────────────────────────────────────────────────────────────────────────

class ErrorLog(Base):
    """Captures every unhandled exception and 5xx/4xx API error for the
    errors dashboard in the admin control panel."""
    __tablename__ = "error_logs"

    id: Mapped[str] = mapped_column(
        String(36), primary_key=True, default=lambda: str(uuid.uuid4())
    )
    # Who caused the error (may be NULL for unauthenticated requests)
    user_id: Mapped[Optional[str]] = mapped_column(String(36), nullable=True)
    user_email: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    tenant_id: Mapped[Optional[str]] = mapped_column(String(36), nullable=True)
    # Request context
    method: Mapped[str] = mapped_column(String(10), nullable=False, default="")
    route: Mapped[str] = mapped_column(String(500), nullable=False, default="")
    status_code: Mapped[int] = mapped_column(Integer, nullable=False, default=500)
    # Error detail
    error_type: Mapped[str] = mapped_column(String(200), nullable=False, default="Exception")
    message: Mapped[str] = mapped_column(Text, nullable=False, default="")
    stack_trace: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    # 'warning' | 'error' | 'critical'
    severity: Mapped[str] = mapped_column(String(20), nullable=False, default="error")
    # Optional correlation ID from X-Request-ID header
    request_id: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    __table_args__ = (
        Index("ix_error_logs_user_id", "user_id"),
        Index("ix_error_logs_tenant_id", "tenant_id"),
        Index("ix_error_logs_severity", "severity"),
        Index("ix_error_logs_created_at", "created_at"),
    )


class AlertEvent(Base):
    """Persisted ops-alert dispatch record (one per send_alert call)."""
    __tablename__ = "alert_events"

    id: Mapped[str] = mapped_column(
        String(36), primary_key=True, default=lambda: str(uuid.uuid4())
    )
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    incident_id: Mapped[str] = mapped_column(String(36), nullable=False, index=True)
    severity: Mapped[str] = mapped_column(String(20), nullable=False, default="critical")
    code: Mapped[str] = mapped_column(String(80), nullable=False, default="")
    title: Mapped[str] = mapped_column(String(300), nullable=False, default="")
    route: Mapped[Optional[str]] = mapped_column(String(500), nullable=True)
    tenant_id: Mapped[Optional[str]] = mapped_column(String(36), nullable=True)
    channels_attempted: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    ai_triage_confidence: Mapped[Optional[float]] = mapped_column(Float, nullable=True)

    __table_args__ = (
        Index("ix_alert_events_created_at", "created_at"),
        Index("ix_alert_events_severity", "severity"),
        Index("ix_alert_events_code", "code"),
    )


class ModelQuotaPolicy(Base):
    """Per-model governance: cost rate, enabled flag, and optional quota cap.
    One row per model; seeded lazily on first access via the admin endpoints."""
    __tablename__ = "model_quota_policies"

    id: Mapped[str] = mapped_column(
        String(36), primary_key=True, default=lambda: str(uuid.uuid4())
    )
    model_id: Mapped[str] = mapped_column(String(100), nullable=False, unique=True)
    display_name: Mapped[Optional[str]] = mapped_column(String(200), nullable=True)
    provider: Mapped[str] = mapped_column(String(100), nullable=False, default="azure_openai")
    is_enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    # Cost in USD per 1 000 tokens (total = prompt + completion)
    cost_rate_per_1k_tokens: Mapped[float] = mapped_column(
        Float, nullable=False, default=0.002
    )
    # Optional hard cap: NULL means unlimited
    daily_token_limit: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    daily_request_limit: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    updated_by: Mapped[Optional[str]] = mapped_column(String(36), nullable=True)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.utcnow, onupdate=datetime.utcnow
    )

    __table_args__ = (
        Index("ix_model_quota_model_id", "model_id"),
    )


class OnboardingLog(Base):
    """Audit trail for automated employee onboarding workflows."""
    __tablename__ = "onboarding_logs"

    id: Mapped[str] = mapped_column(
        String(36), primary_key=True, default=lambda: str(uuid.uuid4())
    )
    # New employee being onboarded
    new_user_email: Mapped[str] = mapped_column(String(255), nullable=False)
    new_user_name: Mapped[str] = mapped_column(String(255), nullable=False)
    department: Mapped[Optional[str]] = mapped_column(String(200), nullable=True)
    manager_email: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    # Admin who triggered the onboarding
    initiated_by: Mapped[str] = mapped_column(String(36), nullable=False)
    initiated_by_email: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    # Which steps were requested and completed
    steps_requested: Mapped[str] = mapped_column(Text, nullable=False, default="[]")   # JSON list
    steps_completed: Mapped[str] = mapped_column(Text, nullable=False, default="[]")   # JSON list
    steps_failed: Mapped[str] = mapped_column(Text, nullable=False, default="[]")      # JSON list
    # Overall outcome
    status: Mapped[str] = mapped_column(String(50), nullable=False, default="pending")
    # 'pending' | 'in_progress' | 'completed' | 'partial' | 'failed'
    notes: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    completed_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)

    __table_args__ = (
        Index("ix_onboarding_logs_new_user_email", "new_user_email"),
        Index("ix_onboarding_logs_initiated_by", "initiated_by"),
        Index("ix_onboarding_logs_created_at", "created_at"),
    )


class HRWorkflowRun(Base):
    """Full audit run record for admin HR workflows (onboarding & offboarding)."""
    __tablename__ = "hr_workflow_runs"

    id: Mapped[str] = mapped_column(
        String(36), primary_key=True, default=lambda: str(uuid.uuid4())
    )
    workflow_type: Mapped[str] = mapped_column(String(20), nullable=False)  # 'onboarding' | 'offboarding'
    # Actor (admin who ran it)
    actor_user_id: Mapped[str] = mapped_column(String(36), nullable=False)
    actor_email: Mapped[str] = mapped_column(String(255), nullable=False)
    # Target identity
    target_email: Mapped[str] = mapped_column(String(255), nullable=False)
    target_upn: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    target_entra_id: Mapped[Optional[str]] = mapped_column(String(36), nullable=True)  # set after creation
    target_display_name: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    # Payload snapshot (full form data as JSON string)
    payload_json: Mapped[str] = mapped_column(Text, nullable=False, default="{}")
    # Step-by-step execution results (JSON list of {step, status, detail})
    step_results_json: Mapped[str] = mapped_column(Text, nullable=False, default="[]")
    # Status
    status: Mapped[str] = mapped_column(String(30), nullable=False, default="running")
    # 'running' | 'completed' | 'partial' | 'failed'
    error_summary: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    approval_reference: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    audit_log_id: Mapped[Optional[str]] = mapped_column(String(36), nullable=True)
    # Timestamps
    started_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    completed_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)

    __table_args__ = (
        Index("ix_hr_workflow_runs_type", "workflow_type"),
        Index("ix_hr_workflow_runs_actor", "actor_user_id"),
        Index("ix_hr_workflow_runs_target", "target_email"),
        Index("ix_hr_workflow_runs_started", "started_at"),
    )


# ─────────────────────────────────────────────────────────────────────────────
# Notification system
# ─────────────────────────────────────────────────────────────────────────────

class NotificationType(str, enum.Enum):
    SHARE_INVITE = "share_invite"
    SHARE_ACCEPTED = "share_accepted"
    BUDGET_WARNING = "budget_warning"
    BUDGET_EXCEEDED = "budget_exceeded"
    SYSTEM = "system"
    MENTION = "mention"
    # Phase 2: emitted by orchestration_ingest when an async worker
    # callback (e.g. Task Radar scan completion) lands.
    WORKER_SCAN_COMPLETE = "worker_scan_complete"


class Notification(Base):
    """In-app notification for a user."""
    __tablename__ = "notifications"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    user_id: Mapped[str] = mapped_column(String(36), ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    type: Mapped[NotificationType] = mapped_column(Enum(NotificationType), nullable=False)
    title: Mapped[str] = mapped_column(String(500), nullable=False)
    message: Mapped[str] = mapped_column(Text, nullable=False)
    # Optional link target (e.g. conversation_id or project_id)
    link_type: Mapped[Optional[str]] = mapped_column(String(50), nullable=True)  # 'conversation' | 'project' | 'admin'
    link_id: Mapped[Optional[str]] = mapped_column(String(36), nullable=True)
    # Actor who triggered this notification (NULL for system notifications)
    actor_id: Mapped[Optional[str]] = mapped_column(String(36), ForeignKey("users.id"), nullable=True)
    is_read: Mapped[bool] = mapped_column(Boolean, default=False)
    is_email_sent: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    # Relationships
    user: Mapped["User"] = relationship("User", foreign_keys=[user_id])
    actor: Mapped[Optional["User"]] = relationship("User", foreign_keys=[actor_id])

    __table_args__ = (
        Index("ix_notifications_user_id", "user_id"),
        Index("ix_notifications_is_read", "user_id", "is_read"),
        Index("ix_notifications_created_at", "created_at"),
    )


# ─────────────────────────────────────────────────────────────────────────────
# Budget governance
# ─────────────────────────────────────────────────────────────────────────────

class UserBudget(Base):
    """Per-user or per-tenant budget configuration with warning/hard-stop thresholds."""
    __tablename__ = "user_budgets"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    # Either user_id or tenant_id must be set (user-level or org-level budget)
    user_id: Mapped[Optional[str]] = mapped_column(String(36), ForeignKey("users.id", ondelete="CASCADE"), nullable=True)
    tenant_id: Mapped[Optional[str]] = mapped_column(String(36), nullable=True)
    # Budget period: 'daily' | 'monthly'
    period: Mapped[str] = mapped_column(String(20), nullable=False, default="monthly")
    # Token budget
    token_budget: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    token_warning_pct: Mapped[int] = mapped_column(Integer, nullable=False, default=80)  # warn at 80%
    # Cost budget (USD)
    cost_budget: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    cost_warning_pct: Mapped[int] = mapped_column(Integer, nullable=False, default=80)
    # Hard stop: if True, block requests when budget exceeded
    hard_stop: Mapped[bool] = mapped_column(Boolean, default=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_by: Mapped[str] = mapped_column(String(36), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    __table_args__ = (
        Index("ix_user_budgets_user_id", "user_id"),
        Index("ix_user_budgets_tenant_id", "tenant_id"),
    )


# ─────────────────────────────────────────────────────────────────────────────
# Three-layer memory system
# ─────────────────────────────────────────────────────────────────────────────

class MemoryType(str, enum.Enum):
    """Types of long-term memory facts."""
    PREFERENCE = "preference"       # User preferences (e.g. "prefers bullet points")
    CORRECTION = "correction"       # Corrections made by user
    FACT = "fact"                   # Facts about user or their work
    CONTEXT = "context"             # Business/project context
    STYLE = "style"                 # Communication style preferences


class UserMemory(Base):
    """Layer 1: Long-term persistent memory for user preferences and facts.

    Stores individual facts extracted from conversations that persist
    across all conversations. Examples:
    - User prefers concise responses
    - User works in marketing department
    - User's manager is named Sarah
    """
    __tablename__ = "user_memories"

    id: Mapped[str] = mapped_column(
        String(36), primary_key=True, default=lambda: str(uuid.uuid4())
    )
    user_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    # Type of memory (preference, correction, fact, etc.)
    memory_type: Mapped[MemoryType] = mapped_column(
        Enum(MemoryType), nullable=False, default=MemoryType.FACT
    )
    # The actual memory content (e.g., "User prefers bullet points")
    content: Mapped[str] = mapped_column(Text, nullable=False)
    # Optional category for grouping (e.g., "communication", "work", "personal")
    category: Mapped[Optional[str]] = mapped_column(String(50), nullable=True)
    # Source conversation where this memory was extracted
    source_conversation_id: Mapped[Optional[str]] = mapped_column(
        String(36), ForeignKey("conversations.id", ondelete="SET NULL"), nullable=True
    )
    # Relevance score (1-10), used for prioritizing in context
    relevance_score: Mapped[int] = mapped_column(Integer, nullable=False, default=5)
    # Number of times this memory has been used/referenced
    usage_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    # Whether this memory is currently active
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    # Profile context: 'work' | 'personal' | 'global' (applies to all)
    profile_scope: Mapped[str] = mapped_column(
        String(20), nullable=False, default="global"
    )
    tenant_id: Mapped[Optional[str]] = mapped_column(String(36), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.utcnow, onupdate=datetime.utcnow
    )

    __table_args__ = (
        Index("ix_user_memories_user_id", "user_id"),
        Index("ix_user_memories_type", "memory_type"),
        Index("ix_user_memories_category", "category"),
        Index("ix_user_memories_profile", "user_id", "profile_scope", "tenant_id"),
        Index("ix_user_memories_active", "user_id", "is_active"),
    )


class SessionMemory(Base):
    """Layer 2: Per-conversation session memory with 30-day expiry.

    Stores compressed summaries of conversation context that can be
    reloaded when the user returns to a conversation. Automatically
    expires after 30 days of inactivity.
    """
    __tablename__ = "session_memories"

    id: Mapped[str] = mapped_column(
        String(36), primary_key=True, default=lambda: str(uuid.uuid4())
    )
    conversation_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("conversations.id", ondelete="CASCADE"),
        nullable=False, unique=True
    )
    user_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    # Compressed summary of the conversation context
    summary: Mapped[str] = mapped_column(Text, nullable=False)
    # Key facts extracted from this conversation (JSON array)
    key_facts: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    # Current conversation goals/objectives (JSON array)
    goals: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    # Entities mentioned in conversation (people, projects, etc.)
    entities: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    # Token count of the summary (for budgeting context window)
    token_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    # Last message ID that was included in this summary
    last_message_id: Mapped[Optional[str]] = mapped_column(String(36), nullable=True)
    # Number of messages summarized
    message_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    # Profile context
    profile_mode: Mapped[str] = mapped_column(
        String(20), nullable=False, default="personal"
    )
    tenant_id: Mapped[Optional[str]] = mapped_column(String(36), nullable=True)
    # Timestamps
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.utcnow, onupdate=datetime.utcnow
    )
    # Auto-expiry: 30 days after last update
    expires_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)

    __table_args__ = (
        Index("ix_session_memories_conversation_id", "conversation_id"),
        Index("ix_session_memories_user_id", "user_id"),
        Index("ix_session_memories_expires", "expires_at"),
        Index("ix_session_memories_profile", "user_id", "profile_mode", "tenant_id"),
    )


# ─────────────────────────────────────────────────────────────────────────────
# Role-based model access
# ─────────────────────────────────────────────────────────────────────────────

class UserModelAccess(Base):
    """Controls which models a user or role can access.
    If no rows exist for a user, they can access all enabled models."""
    __tablename__ = "user_model_access"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    # Target: either a specific user or a role
    user_id: Mapped[Optional[str]] = mapped_column(String(36), ForeignKey("users.id", ondelete="CASCADE"), nullable=True)
    role: Mapped[Optional[str]] = mapped_column(String(50), nullable=True)  # 'admin' | 'user' | 'viewer'
    model_id: Mapped[str] = mapped_column(String(100), nullable=False)
    is_allowed: Mapped[bool] = mapped_column(Boolean, default=True)
    created_by: Mapped[str] = mapped_column(String(36), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    __table_args__ = (
        Index("ix_user_model_access_user_id", "user_id"),
        Index("ix_user_model_access_role", "role"),
        Index("ix_user_model_access_model_id", "model_id"),
    )


# ─────────────────────────────────────────────────────────────────────────────
# Connector state (delta tokens, sync cursors)
# ─────────────────────────────────────────────────────────────────────────────

class ConnectorState(Base):
    """Persisted key-value state for connector sync (delta tokens, cursors).

    Survives app restarts so delta syncs resume incrementally instead of
    performing a full re-crawl.
    """
    __tablename__ = "connector_state"

    id: Mapped[str] = mapped_column(
        String(36), primary_key=True, default=lambda: str(uuid.uuid4())
    )
    connector_type: Mapped[str] = mapped_column(String(50), nullable=False)
    source_id: Mapped[str] = mapped_column(String(500), nullable=False, unique=True)
    state_key: Mapped[str] = mapped_column(String(100), nullable=False, default="delta_token")
    state_value: Mapped[str] = mapped_column(Text, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.utcnow, onupdate=datetime.utcnow
    )

    __table_args__ = (
        Index("ix_connector_state_source", "connector_type", "source_id"),
    )


# ─────────────────────────────────────────────────────────────────────────────
# Agent Memory: user-curated knowledge layer (docs, websites, templates)
# ─────────────────────────────────────────────────────────────────────────────

class AgentMemoryItem(Base):
    """A single piece of Agent Memory: an uploaded file, a crawled site, or a template.

    Items are owned by a user and live in a scope:
      - 'personal'  → only the owner can use them in retrieval
      - 'workspace' → all members of the user's tenant can use them
      - 'tenant'    → tenant-wide knowledge (admin-only writes)

    Tags steer retrieval bias: 'knowledge' (default), 'template', 'brand',
    'policy', 'demo'. Templates additionally store a parsed schema in
    template_schema_json so the chat layer can fill them deterministically.

    Status is a small state machine driven by the ingestion worker:
      pending → parsing → embedding → ready  (happy path)
                       → crawling → embedding → ready  (websites)
                       → failed                          (error_message set)
    """
    __tablename__ = "agent_memory_items"

    id: Mapped[str] = mapped_column(
        String(36), primary_key=True, default=lambda: str(uuid.uuid4())
    )
    user_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    tenant_id: Mapped[Optional[str]] = mapped_column(String(36), nullable=True)
    # 'personal' | 'workspace' | 'tenant'
    scope: Mapped[str] = mapped_column(String(20), nullable=False, default="personal")
    # 'knowledge' | 'template' | 'brand' | 'policy' | 'demo'
    tag: Mapped[str] = mapped_column(String(20), nullable=False, default="knowledge")
    # 'upload' | 'web' | 'sharepoint' | 'onedrive'
    source_type: Mapped[str] = mapped_column(String(20), nullable=False)
    # connector-side id (URL for web, file path/id for upload, etc.)
    source_id: Mapped[str] = mapped_column(String(500), nullable=False)
    title: Mapped[str] = mapped_column(String(500), nullable=False)
    url: Mapped[Optional[str]] = mapped_column(String(2000), nullable=True)
    blob_url: Mapped[Optional[str]] = mapped_column(String(2000), nullable=True)
    file_type: Mapped[Optional[str]] = mapped_column(String(50), nullable=True)
    file_size: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    # SHA-256 of normalised content; used to dedupe re-uploads of the same file
    content_hash: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    # 'pending' | 'parsing' | 'crawling' | 'embedding' | 'ready' | 'failed'
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="pending")
    error_message: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    chunk_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    page_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    # Parsed schema for items with tag='template'. JSON shape:
    # { "sections": [{ "heading", "order", "placeholders": [...], "style_hints", "example_text" }],
    #   "tone_summary": str, "branding": { ... } }
    template_schema_json: Mapped[Optional[dict]] = mapped_column(JSON, nullable=True)
    # Per-conversation soft-disable so users can mute an item for a session
    # without deleting it. Map of conversation_id → True.
    session_disabled: Mapped[Optional[dict]] = mapped_column(JSON, nullable=True)
    last_synced_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.utcnow, onupdate=datetime.utcnow
    )

    __table_args__ = (
        Index("ix_agent_memory_owner", "user_id", "scope", "status"),
        Index("ix_agent_memory_tenant", "tenant_id", "scope", "tag"),
        Index("ix_agent_memory_status", "status"),
        UniqueConstraint(
            "user_id", "content_hash",
            name="uq_agent_memory_user_hash",
        ),
    )


# ── Orchestration Brain: Worker Registry ─────────────────────────────────
#
# Source of truth for which independent worker apps Mela can orchestrate.
# Adding a new worker requires only a row here + an adapter file — no
# changes to the orchestration engine.  The full WorkerManifest pydantic
# shape is stored in the `manifest` JSON column; the flat columns
# duplicate the most-queried fields for fast filtering / health views.
class WorkerRegistryEntry(Base):
    __tablename__ = "worker_registry"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    display_name: Mapped[str] = mapped_column(String(200), nullable=False)
    version: Mapped[str] = mapped_column(String(40), nullable=False)
    protocol: Mapped[str] = mapped_column(String(20), nullable=False)
    base_url: Mapped[str] = mapped_column(String(500), nullable=False)
    health_check_url: Mapped[str] = mapped_column(String(500), nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="unknown")
    manifest: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    last_health_check: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    registered_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.utcnow, onupdate=datetime.utcnow
    )

    __table_args__ = (
        Index("ix_worker_registry_status", "status"),
    )


# ── Orchestration Brain: trace + task + event tables ─────────────────────
#
# OrchestrationTrace = one user-initiated goal that may fan out into many
#   adapter calls.  Trace IDs are minted on the inbound chat path and
#   propagate through MelaTask.trace_id so every worker call is grouped
#   under the same trace for end-to-end debugging.
#
# OrchestrationTask = one MelaTask issued under a trace.  Persisted before
#   the adapter call goes out, updated when the result comes back (sync)
#   or when the worker POSTs to /ingest/result (async).
#
# WorkerEvent = unsolicited push from a worker (e.g. scan.completed).
#   Kept separate from OrchestrationTask because events aren't tied to
#   any outbound task we issued — workers initiate them.
class OrchestrationTrace(Base):
    """A single goal-execution trace spanning one or more worker tasks."""
    __tablename__ = "orchestration_traces"

    trace_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    goal_id: Mapped[str] = mapped_column(String(36), nullable=False)
    user_id: Mapped[str] = mapped_column(String(36), nullable=False)
    tenant_id: Mapped[Optional[str]] = mapped_column(String(36), nullable=True)
    profile_mode: Mapped[str] = mapped_column(String(20), nullable=False, default="personal")
    # 'pending' | 'running' | 'completed' | 'failed' | 'partial'
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="pending")
    # Optional plan snapshot for debugging.  Empty {} when the trace was
    # spawned by a single tool call rather than a multi-task plan.
    plan_json: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    completed_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)

    __table_args__ = (
        Index("ix_orch_traces_user", "user_id", "created_at"),
        Index("ix_orch_traces_status", "status"),
        Index("ix_orch_traces_tenant", "tenant_id", "created_at"),
    )


class OrchestrationTask(Base):
    """A single MelaTask issued by the executor under a parent trace."""
    __tablename__ = "orchestration_tasks"

    task_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    trace_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey("orchestration_traces.trace_id", ondelete="CASCADE"),
        nullable=False,
    )
    worker_id: Mapped[str] = mapped_column(String(64), nullable=False)
    capability: Mapped[str] = mapped_column(String(128), nullable=False)
    # 'sync' | 'async'
    execution_mode: Mapped[str] = mapped_column(String(10), nullable=False, default="sync")
    # 'pending' | 'running' | 'awaiting_callback' | 'completed' | 'failed'
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="pending")
    params_json: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    summary: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    # Pointer to full data (e.g. blob URI, KB entry ID).  Raw worker data
    # is NOT stored on this row — keep it lean for fast queries.
    data_pointer: Mapped[Optional[str]] = mapped_column(String(500), nullable=True)
    error_code: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    error_message: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    latency_ms: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    completed_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)

    __table_args__ = (
        Index("ix_orch_tasks_trace", "trace_id"),
        Index("ix_orch_tasks_worker_capability", "worker_id", "capability"),
        Index("ix_orch_tasks_status", "status"),
    )


class WorkerEvent(Base):
    """Unsolicited event pushed by a worker via /api/v1/ingest/event."""
    __tablename__ = "worker_events"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    worker_id: Mapped[str] = mapped_column(String(64), nullable=False)
    event_type: Mapped[str] = mapped_column(String(128), nullable=False)
    payload_json: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    # Optional: which user / tenant this event concerns.  Null for system-wide events.
    user_id: Mapped[Optional[str]] = mapped_column(String(36), nullable=True)
    tenant_id: Mapped[Optional[str]] = mapped_column(String(36), nullable=True)
    received_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    processed_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)

    __table_args__ = (
        Index("ix_worker_events_worker", "worker_id", "received_at"),
        Index("ix_worker_events_type", "event_type"),
        Index("ix_worker_events_user", "user_id", "received_at"),
    )


# ── Orchestration Brain: Knowledge Base ──────────────────────────────────
#
# What Mela remembers across conversations: the LLM-readable ``summary``
# of every worker result + key system events.  Raw worker data stays in
# the worker; the KB stores a pointer + a 2-3 sentence summary.  The
# planner / synthesiser reads ``summary`` only — never the full data —
# which keeps Mela's context window lean as workers multiply.
class KnowledgeEntry(Base):
    """A single piece of context Mela remembers across conversations.

    ``embedding_vector`` is reserved for Phase 4 vector search via Azure
    AI Search.  Phase 3 keyword-matches over ``title + summary`` only.
    """
    __tablename__ = "knowledge_entries"

    entry_id: Mapped[str] = mapped_column(
        String(36), primary_key=True, default=lambda: str(uuid.uuid4())
    )
    tenant_id: Mapped[Optional[str]] = mapped_column(String(36), nullable=True)
    user_id: Mapped[str] = mapped_column(String(36), nullable=False)
    profile_mode: Mapped[str] = mapped_column(
        String(20), nullable=False, default="personal"
    )
    source_worker_id: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    trace_id: Mapped[Optional[str]] = mapped_column(
        String(36),
        ForeignKey("orchestration_traces.trace_id", ondelete="SET NULL"),
        nullable=True,
    )
    # 'task_summary' | 'meeting_summary' | 'goal_result' | 'worker_event' | 'user_context'
    entry_type: Mapped[str] = mapped_column(String(40), nullable=False)
    title: Mapped[str] = mapped_column(String(500), nullable=False)
    # Hard 500-char cap — this is what the LLM reads.  Summariser must
    # collapse longer worker output before insert.
    summary: Mapped[str] = mapped_column(String(500), nullable=False)
    # Pointer back to the source: "<worker_id>:<id>" or a URL.  Full data
    # is fetched on demand from the source, never re-stored here.
    data_pointer: Mapped[Optional[str]] = mapped_column(String(500), nullable=True)
    tags: Mapped[Optional[list]] = mapped_column(JSON, nullable=True)
    # JSON-serialised float array for now; Phase 4 will move embeddings
    # into Azure AI Search and reference them by document_id.
    embedding_vector: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    expires_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)

    __table_args__ = (
        Index("ix_kb_owner", "tenant_id", "user_id", "created_at"),
        Index("ix_kb_user", "user_id", "created_at"),
        Index("ix_kb_type", "entry_type"),
        Index("ix_kb_trace", "trace_id"),
        Index("ix_kb_worker", "source_worker_id", "created_at"),
    )


# Phase 5C: per-tenant worker access grants.  When
# ``WORKER_ACCESS_DEFAULT_ALLOW`` is True (the default) this table is
# never consulted — current behaviour is preserved.  When False, only
# tenants with a row here AND ``revoked_at IS NULL`` can invoke the
# corresponding worker.  Soft-delete only — audit trail matters.
class WorkerTenantAccess(Base):
    __tablename__ = "worker_tenant_access"

    id: Mapped[str] = mapped_column(
        String(36), primary_key=True, default=lambda: str(uuid.uuid4())
    )
    worker_id: Mapped[str] = mapped_column(
        String(64),
        ForeignKey("worker_registry.id", ondelete="CASCADE"),
        nullable=False,
    )
    tenant_id: Mapped[str] = mapped_column(String(36), nullable=False)
    granted_at: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.utcnow
    )
    granted_by: Mapped[str] = mapped_column(String(36), nullable=False)
    # Soft-delete only — keep audit history forever.
    revoked_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime, nullable=True
    )

    __table_args__ = (
        Index(
            "ix_worker_tenant_access_lookup",
            "worker_id", "tenant_id", "revoked_at",
        ),
        Index("ix_worker_tenant_access_tenant", "tenant_id"),
    )


# Phase 6A: external apps that call Mela's own MCP server.  The API
# key is bcrypt-hashed on storage; plaintext is returned EXACTLY ONCE
# at creation time and never again.  Soft-delete only — audit trail
# matters here too.
class MCPClient(Base):
    __tablename__ = "mcp_clients"

    id: Mapped[str] = mapped_column(
        String(36), primary_key=True, default=lambda: str(uuid.uuid4())
    )
    client_name: Mapped[str] = mapped_column(String(200), nullable=False)
    # bcrypt hash of the issued API key; never stores plaintext.
    api_key_hash: Mapped[str] = mapped_column(String(200), nullable=False)
    tenant_id: Mapped[Optional[str]] = mapped_column(String(36), nullable=True)
    # JSON list of allowed tool names (e.g. ["mela_chat", "mela_search_knowledge"])
    # Empty list = no tools allowed.  Use ["*"] for full access.
    scopes: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    created_by: Mapped[Optional[str]] = mapped_column(String(36), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    revoked_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    last_used_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)

    __table_args__ = (
        Index("ix_mcp_clients_revoked", "revoked_at"),
        Index("ix_mcp_clients_tenant", "tenant_id"),
    )
