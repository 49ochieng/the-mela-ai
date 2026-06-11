"""sessions + oauth_states tables

Revision ID: 0008_sessions_oauth_states
Revises: 0007_agent_tokens
Create Date: 2026-05-11
"""
from __future__ import annotations

from typing import Union

import sqlalchemy as sa
from alembic import op

revision: str = "0008_sessions_oauth_states"
down_revision: Union[str, None] = "0007_agent_tokens"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "sessions",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column("tenant_id", sa.String(length=36), nullable=False, index=True),
        sa.Column("user_id", sa.String(length=36), sa.ForeignKey("users.id"), nullable=False, index=True),
        sa.Column("jti", sa.String(length=36), nullable=False, unique=True, index=True),
        sa.Column("issued_at", sa.DateTime, nullable=False),
        sa.Column("last_seen_at", sa.DateTime, nullable=False),
        sa.Column("expires_at", sa.DateTime, nullable=False),
        sa.Column("ip_hash", sa.String(length=64), nullable=True),
        sa.Column("ua_hash", sa.String(length=64), nullable=True),
        sa.Column("revoked_at", sa.DateTime, nullable=True),
        sa.Column("created_at", sa.DateTime, nullable=False),
        sa.Column("updated_at", sa.DateTime, nullable=False),
    )
    op.create_table(
        "oauth_states",
        sa.Column("state", sa.String(length=64), primary_key=True),
        sa.Column("nonce", sa.String(length=64), nullable=False, index=True),
        sa.Column("flow_json", sa.JSON, nullable=False),
        sa.Column("created_at", sa.DateTime, nullable=False),
        sa.Column("expires_at", sa.DateTime, nullable=False, index=True),
        sa.Column("consumed_at", sa.DateTime, nullable=True),
    )


def downgrade() -> None:
    op.drop_table("oauth_states")
    op.drop_table("sessions")
