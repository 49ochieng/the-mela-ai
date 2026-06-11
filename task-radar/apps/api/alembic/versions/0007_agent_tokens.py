"""agent_tokens table

Revision ID: 0007_agent_tokens
Revises: 0006_default_user_timezone_ct
Create Date: 2026-05-11
"""
from __future__ import annotations

from typing import Union

import sqlalchemy as sa
from alembic import op

revision: str = "0007_agent_tokens"
down_revision: Union[str, None] = "0006_default_user_timezone_ct"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "agent_tokens",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column("tenant_id", sa.String(length=36), nullable=False, index=True),
        sa.Column("user_id", sa.String(length=36), sa.ForeignKey("users.id"), nullable=False, index=True),
        sa.Column("name", sa.String(length=128), nullable=False),
        sa.Column("token_hash", sa.String(length=64), nullable=False, unique=True, index=True),
        sa.Column("scopes_json", sa.JSON, nullable=False, server_default=sa.text("'{}'")),
        sa.Column("last_used_at", sa.DateTime, nullable=True),
        sa.Column("expires_at", sa.DateTime, nullable=True),
        sa.Column("revoked_at", sa.DateTime, nullable=True),
        sa.Column("created_at", sa.DateTime, nullable=False),
        sa.Column("updated_at", sa.DateTime, nullable=False),
    )


def downgrade() -> None:
    op.drop_table("agent_tokens")
