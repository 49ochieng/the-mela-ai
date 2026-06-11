"""default user timezone to America/Chicago (CT)

Revision ID: 0006_default_user_timezone_ct
Revises: 0005_default_auto_scan_on
Create Date: 2026-05-13
"""
from __future__ import annotations

from typing import Union

from alembic import op

revision: str = "0006_default_user_timezone_ct"
down_revision: Union[str, None] = "0005_default_auto_scan_on"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Flip existing UTC users to America/Chicago so the per-user scheduler
    # honors the product's default timezone. Users can change to any allowed
    # zone via Settings → Scan.
    op.execute(
        "UPDATE users SET timezone = 'America/Chicago' WHERE timezone = 'UTC' OR timezone IS NULL"
    )


def downgrade() -> None:
    op.execute(
        "UPDATE users SET timezone = 'UTC' WHERE timezone = 'America/Chicago'"
    )
