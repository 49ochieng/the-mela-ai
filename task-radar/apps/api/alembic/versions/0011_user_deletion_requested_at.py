"""user.deletion_requested_at

Adds the timestamp column the privacy router stamps on
``POST /api/me/delete`` so the account_deleter worker can hard-delete
rows after the configured grace window.

Revision ID: 0011_user_deletion_requested_at
Revises: 0010_audit_chain
Create Date: 2026-05-12
"""
from __future__ import annotations

from typing import Union

import sqlalchemy as sa
from alembic import op

revision: str = "0011_user_deletion_requested_at"
down_revision: Union[str, None] = "0010_audit_chain"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("users") as b:
        b.add_column(sa.Column("deletion_requested_at", sa.DateTime, nullable=True))
    op.create_index(
        "ix_users_deletion_requested_at",
        "users",
        ["deletion_requested_at"],
    )


def downgrade() -> None:
    op.drop_index("ix_users_deletion_requested_at", table_name="users")
    with op.batch_alter_table("users") as b:
        b.drop_column("deletion_requested_at")
