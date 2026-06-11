"""nullable scan_events.source_type for system stages

Revision ID: 0004_nullable_scan_event_source
Revises: 0003_auto_sync_and_priority_score
Create Date: 2026-05-06
"""
from __future__ import annotations

from typing import Union

import sqlalchemy as sa
from alembic import op

revision: str = "0004_nullable_scan_event_source"
down_revision: Union[str, None] = "0003_auto_sync_and_priority_score"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("scan_events") as batch:
        batch.alter_column("source_type", existing_type=sa.String(16), nullable=True)


def downgrade() -> None:
    with op.batch_alter_table("scan_events") as batch:
        batch.alter_column("source_type", existing_type=sa.String(16), nullable=False)
