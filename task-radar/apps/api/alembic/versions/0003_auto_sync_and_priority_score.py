"""auto-sync settings + priority_score

Revision ID: 0003_auto_sync_and_priority_score
Revises: 0002_scan_diagnostics
Create Date: 2026-05-06
"""
from __future__ import annotations

from typing import Union

import sqlalchemy as sa
from alembic import op

revision: str = "0003_auto_sync_and_priority_score"
down_revision: Union[str, None] = "0002_scan_diagnostics"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("tasks") as batch:
        batch.add_column(
            sa.Column("priority_score", sa.Integer(), nullable=False, server_default="0")
        )

    with op.batch_alter_table("scan_settings") as batch:
        # none | high | high_medium | all
        batch.add_column(
            sa.Column(
                "auto_sync_to_planner_priority",
                sa.String(16),
                nullable=False,
                server_default="none",
            )
        )
        batch.add_column(
            sa.Column(
                "auto_archive_to_excel",
                sa.Boolean(),
                nullable=False,
                server_default=sa.true(),
            )
        )

    op.create_index("ix_tasks_priority_score", "tasks", ["priority_score"])


def downgrade() -> None:
    op.drop_index("ix_tasks_priority_score", table_name="tasks")
    with op.batch_alter_table("scan_settings") as batch:
        batch.drop_column("auto_archive_to_excel")
        batch.drop_column("auto_sync_to_planner_priority")
    with op.batch_alter_table("tasks") as batch:
        batch.drop_column("priority_score")
