"""initial schema

Revision ID: 0001_initial
Revises:
Create Date: 2026-04-30
"""
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0001_initial"
down_revision: Union[str, None] = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "tenants",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("entra_tenant_id", sa.String(64), nullable=False, unique=True),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("created_at", sa.DateTime, nullable=False),
        sa.Column("updated_at", sa.DateTime, nullable=False),
    )
    op.create_table(
        "users",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("tenant_id", sa.String(36), sa.ForeignKey("tenants.id"), nullable=False),
        sa.Column("entra_user_id", sa.String(64), nullable=False),
        sa.Column("display_name", sa.String(255), nullable=False),
        sa.Column("email", sa.String(320), nullable=False),
        sa.Column("timezone", sa.String(64), nullable=False, server_default="UTC"),
        sa.Column("role", sa.String(16), nullable=False, server_default="user"),
        sa.Column("created_at", sa.DateTime, nullable=False),
        sa.Column("updated_at", sa.DateTime, nullable=False),
        sa.UniqueConstraint("tenant_id", "entra_user_id"),
    )
    op.create_index("ix_users_tenant_id", "users", ["tenant_id"])
    op.create_index("ix_users_email", "users", ["email"])

    op.create_table(
        "graph_connections",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("tenant_id", sa.String(36), nullable=False),
        sa.Column("user_id", sa.String(36), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("provider", sa.String(32), nullable=False, server_default="microsoft"),
        sa.Column("scopes", sa.Text, nullable=False, server_default=""),
        sa.Column("status", sa.String(32), nullable=False, server_default="disconnected"),
        sa.Column("token_reference", sa.Text),
        sa.Column("refresh_token_reference", sa.Text),
        sa.Column("expires_at", sa.DateTime),
        sa.Column("last_connected_at", sa.DateTime),
        sa.Column("created_at", sa.DateTime, nullable=False),
        sa.Column("updated_at", sa.DateTime, nullable=False),
    )

    op.create_table(
        "scan_settings",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("tenant_id", sa.String(36), nullable=False),
        sa.Column("user_id", sa.String(36), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("email_scan_enabled", sa.Boolean, nullable=False, server_default=sa.true()),
        sa.Column("teams_scan_enabled", sa.Boolean, nullable=False, server_default=sa.false()),
        sa.Column("daily_scan_enabled", sa.Boolean, nullable=False, server_default=sa.false()),
        sa.Column("scan_time_local", sa.Time, nullable=False),
        sa.Column("timezone", sa.String(64), nullable=False, server_default="UTC"),
        sa.Column("last_email_scan_at", sa.DateTime),
        sa.Column("last_teams_scan_at", sa.DateTime),
        sa.Column("selected_team_ids", sa.JSON, nullable=False),
        sa.Column("selected_channel_ids", sa.JSON, nullable=False),
        sa.Column("excel_sync_enabled", sa.Boolean, nullable=False, server_default=sa.false()),
        sa.Column("planner_sync_enabled", sa.Boolean, nullable=False, server_default=sa.false()),
        sa.Column("planner_plan_id", sa.String(128)),
        sa.Column("planner_bucket_id", sa.String(128)),
        sa.Column("approval_required_for_planner", sa.Boolean, nullable=False, server_default=sa.true()),
        sa.Column("mentions_only", sa.Boolean, nullable=False, server_default=sa.true()),
        sa.Column("lookback_hours_first_scan", sa.Integer, nullable=False, server_default="72"),
        sa.Column("created_at", sa.DateTime, nullable=False),
        sa.Column("updated_at", sa.DateTime, nullable=False),
        sa.UniqueConstraint("tenant_id", "user_id"),
    )

    op.create_table(
        "scan_runs",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("tenant_id", sa.String(36), nullable=False),
        sa.Column("user_id", sa.String(36), nullable=False),
        sa.Column("scan_type", sa.String(16), nullable=False),
        sa.Column("source_scope", sa.JSON, nullable=False),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("started_at", sa.DateTime),
        sa.Column("completed_at", sa.DateTime),
        sa.Column("messages_scanned", sa.Integer, nullable=False, server_default="0"),
        sa.Column("messages_skipped", sa.Integer, nullable=False, server_default="0"),
        sa.Column("tasks_found", sa.Integer, nullable=False, server_default="0"),
        sa.Column("tasks_created", sa.Integer, nullable=False, server_default="0"),
        sa.Column("tasks_deduped", sa.Integer, nullable=False, server_default="0"),
        sa.Column("errors_count", sa.Integer, nullable=False, server_default="0"),
        sa.Column("error_summary", sa.Text),
        sa.Column("created_at", sa.DateTime, nullable=False),
        sa.Column("updated_at", sa.DateTime, nullable=False),
    )

    op.create_table(
        "source_messages",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("tenant_id", sa.String(36), nullable=False),
        sa.Column("user_id", sa.String(36), nullable=False),
        sa.Column("source_type", sa.String(16), nullable=False),
        sa.Column("graph_message_id", sa.String(255), nullable=False),
        sa.Column("internet_message_id", sa.String(512)),
        sa.Column("conversation_id", sa.String(255)),
        sa.Column("reply_to_id", sa.String(255)),
        sa.Column("sender_name", sa.String(255)),
        sa.Column("sender_email", sa.String(320)),
        sa.Column("recipients_json", sa.JSON, nullable=False),
        sa.Column("subject_or_channel", sa.String(1024)),
        sa.Column("body_excerpt", sa.Text),
        sa.Column("body_hash", sa.String(64)),
        sa.Column("source_link", sa.Text),
        sa.Column("received_at", sa.DateTime),
        sa.Column("processed_at", sa.DateTime),
        sa.Column("has_attachments", sa.Boolean, nullable=False, server_default=sa.false()),
        sa.Column("raw_metadata_json", sa.JSON, nullable=False),
        sa.Column("created_at", sa.DateTime, nullable=False),
        sa.Column("updated_at", sa.DateTime, nullable=False),
        sa.UniqueConstraint("tenant_id", "user_id", "source_type", "graph_message_id"),
    )

    op.create_table(
        "tasks",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("tenant_id", sa.String(36), nullable=False),
        sa.Column("user_id", sa.String(36), nullable=False),
        sa.Column("source_message_id", sa.String(36), sa.ForeignKey("source_messages.id")),
        sa.Column("title", sa.String(512), nullable=False),
        sa.Column("description", sa.Text),
        sa.Column("task_type", sa.String(32), nullable=False),
        sa.Column("assigned_to", sa.String(320)),
        sa.Column("due_date", sa.DateTime),
        sa.Column("due_date_raw", sa.String(255)),
        sa.Column("priority", sa.String(16), nullable=False),
        sa.Column("priority_reasoning", sa.Text),
        sa.Column("confidence", sa.Float, nullable=False, server_default="0"),
        sa.Column("evidence", sa.Text),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("source_type", sa.String(16), nullable=False),
        sa.Column("source_link", sa.Text),
        sa.Column("created_at", sa.DateTime, nullable=False),
        sa.Column("updated_at", sa.DateTime, nullable=False),
    )
    op.create_index("ix_tasks_tenant_user_status", "tasks", ["tenant_id", "user_id", "status"])
    op.create_index("ix_tasks_due_date", "tasks", ["due_date"])

    op.create_table(
        "task_attachments",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("tenant_id", sa.String(36), nullable=False),
        sa.Column("user_id", sa.String(36), nullable=False),
        sa.Column("task_id", sa.String(36), sa.ForeignKey("tasks.id"), nullable=False),
        sa.Column("source_message_id", sa.String(36)),
        sa.Column("source_attachment_id", sa.String(255)),
        sa.Column("file_name", sa.String(512), nullable=False),
        sa.Column("content_type", sa.String(255)),
        sa.Column("size_bytes", sa.Integer),
        sa.Column("source_url", sa.Text),
        sa.Column("archive_url", sa.Text),
        sa.Column("storage_status", sa.String(32), nullable=False),
        sa.Column("created_at", sa.DateTime, nullable=False),
        sa.Column("updated_at", sa.DateTime, nullable=False),
    )

    op.create_table(
        "task_syncs",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("tenant_id", sa.String(36), nullable=False),
        sa.Column("user_id", sa.String(36), nullable=False),
        sa.Column("task_id", sa.String(36), sa.ForeignKey("tasks.id"), nullable=False),
        sa.Column("target_type", sa.String(32), nullable=False),
        sa.Column("target_id", sa.String(255)),
        sa.Column("target_url", sa.Text),
        sa.Column("sync_status", sa.String(32), nullable=False),
        sa.Column("error_message", sa.Text),
        sa.Column("synced_at", sa.DateTime),
        sa.Column("created_at", sa.DateTime, nullable=False),
        sa.Column("updated_at", sa.DateTime, nullable=False),
    )

    op.create_table(
        "audit_logs",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("tenant_id", sa.String(36), nullable=False),
        sa.Column("user_id", sa.String(36)),
        sa.Column("action", sa.String(128), nullable=False),
        sa.Column("entity_type", sa.String(64)),
        sa.Column("entity_id", sa.String(64)),
        sa.Column("details_json", sa.JSON, nullable=False),
        sa.Column("created_at", sa.DateTime, nullable=False),
    )


def downgrade() -> None:
    for t in [
        "audit_logs", "task_syncs", "task_attachments", "tasks",
        "source_messages", "scan_runs", "scan_settings",
        "graph_connections", "users", "tenants",
    ]:
        op.drop_table(t)
