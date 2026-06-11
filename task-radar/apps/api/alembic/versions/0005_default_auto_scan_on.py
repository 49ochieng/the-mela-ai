"""enable auto-scan + smart teams + excel/planner defaults

Revision ID: 0005_default_auto_scan_on
Revises: 0004_nullable_scan_event_source
Create Date: 2026-05-06
"""
from __future__ import annotations

from typing import Union

from alembic import op

revision: str = "0005_default_auto_scan_on"
down_revision: Union[str, None] = "0004_nullable_scan_event_source"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Flip existing rows to the new opt-out defaults so the cadence works
    # immediately for everyone signed in. New columns default at the model
    # layer; here we just bring legacy rows up to date.
    op.execute(
        """
        UPDATE scan_settings
        SET daily_scan_enabled = 1,
            teams_scan_enabled = 1,
            excel_sync_enabled = 1,
            planner_sync_enabled = 1,
            mentions_only = 0,
            include_thread_context = 1,
            approval_required_for_planner = 0,
            auto_sync_to_planner_priority = CASE
                WHEN auto_sync_to_planner_priority = 'none' THEN 'high_medium'
                ELSE auto_sync_to_planner_priority
            END,
            max_messages_per_scan = CASE
                WHEN max_messages_per_scan < 1000 THEN 1000
                ELSE max_messages_per_scan
            END,
            max_ai_calls_per_scan = CASE
                WHEN max_ai_calls_per_scan < 400 THEN 400
                ELSE max_ai_calls_per_scan
            END,
            timezone = CASE
                WHEN timezone = 'UTC' OR timezone IS NULL THEN 'America/Chicago'
                ELSE timezone
            END
        """
    )


def downgrade() -> None:
    # Non-destructive downgrade: leave user toggles alone.
    pass
