"""scan diagnostics + teams settings + scan_events

Revision ID: 0002_scan_diagnostics
Revises: 0001_initial
Create Date: 2026-05-04
"""
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0002_scan_diagnostics"
down_revision: Union[str, None] = "0001_initial"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # ── ScanRun: per-stage diagnostic counters ──────────────────────
    with op.batch_alter_table("scan_runs") as batch:
        batch.add_column(sa.Column("noise_skipped_count", sa.Integer(), nullable=False, server_default="0"))
        batch.add_column(sa.Column("duplicate_skipped_count", sa.Integer(), nullable=False, server_default="0"))
        batch.add_column(sa.Column("ai_attempted_count", sa.Integer(), nullable=False, server_default="0"))
        batch.add_column(sa.Column("ai_success_count", sa.Integer(), nullable=False, server_default="0"))
        batch.add_column(sa.Column("ai_no_task_count", sa.Integer(), nullable=False, server_default="0"))
        batch.add_column(sa.Column("ai_failed_count", sa.Integer(), nullable=False, server_default="0"))
        batch.add_column(sa.Column("needs_review_count", sa.Integer(), nullable=False, server_default="0"))
        batch.add_column(sa.Column("attachment_failed_count", sa.Integer(), nullable=False, server_default="0"))
        batch.add_column(sa.Column("excel_failed_count", sa.Integer(), nullable=False, server_default="0"))
        batch.add_column(sa.Column("planner_failed_count", sa.Integer(), nullable=False, server_default="0"))
        batch.add_column(sa.Column("error_categories_json", sa.JSON(), nullable=False, server_default="{}"))

    # ── ScanSettings: include_thread_context, budgets, delta link ───
    with op.batch_alter_table("scan_settings") as batch:
        batch.add_column(sa.Column("include_thread_context", sa.Boolean(), nullable=False, server_default=sa.false()))
        batch.add_column(sa.Column("max_messages_per_scan", sa.Integer(), nullable=False, server_default="500"))
        batch.add_column(sa.Column("max_ai_calls_per_scan", sa.Integer(), nullable=False, server_default="200"))
        batch.add_column(sa.Column("email_delta_link", sa.Text(), nullable=True))
        batch.add_column(sa.Column("email_delta_token", sa.Text(), nullable=True))

    # ── scan_events: per-message diagnostic record ──────────────────
    op.create_table(
        "scan_events",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("tenant_id", sa.String(36), nullable=False),
        sa.Column("user_id", sa.String(36), nullable=False),
        sa.Column("scan_run_id", sa.String(36), sa.ForeignKey("scan_runs.id"), nullable=False),
        sa.Column("source_type", sa.String(16), nullable=False),
        sa.Column("graph_message_id", sa.String(512)),
        sa.Column("stage", sa.String(32), nullable=False),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("category", sa.String(64)),
        sa.Column("message", sa.Text()),
        sa.Column("retryable", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("details_json", sa.JSON(), nullable=False, server_default="{}"),
        sa.Column("created_at", sa.DateTime(), nullable=False),
    )
    op.create_index("ix_scan_events_scan_run_id", "scan_events", ["scan_run_id"])
    op.create_index("ix_scan_events_stage", "scan_events", ["stage"])
    op.create_index("ix_scan_events_status", "scan_events", ["status"])
    op.create_index("ix_scan_events_created_at", "scan_events", ["created_at"])


def downgrade() -> None:
    op.drop_index("ix_scan_events_created_at", table_name="scan_events")
    op.drop_index("ix_scan_events_status", table_name="scan_events")
    op.drop_index("ix_scan_events_stage", table_name="scan_events")
    op.drop_index("ix_scan_events_scan_run_id", table_name="scan_events")
    op.drop_table("scan_events")

    with op.batch_alter_table("scan_settings") as batch:
        batch.drop_column("email_delta_token")
        batch.drop_column("email_delta_link")
        batch.drop_column("max_ai_calls_per_scan")
        batch.drop_column("max_messages_per_scan")
        batch.drop_column("include_thread_context")

    with op.batch_alter_table("scan_runs") as batch:
        for col in (
            "error_categories_json", "planner_failed_count", "excel_failed_count",
            "attachment_failed_count", "needs_review_count", "ai_failed_count",
            "ai_no_task_count", "ai_success_count", "ai_attempted_count",
            "duplicate_skipped_count", "noise_skipped_count",
        ):
            batch.drop_column(col)
