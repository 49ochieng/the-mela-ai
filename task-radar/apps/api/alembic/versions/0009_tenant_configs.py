"""tenant_configs table

Revision ID: 0009_tenant_configs
Revises: 0008_sessions_oauth_states
Create Date: 2026-05-11
"""
from __future__ import annotations

from typing import Union

import sqlalchemy as sa
from alembic import op

revision: str = "0009_tenant_configs"
down_revision: Union[str, None] = "0008_sessions_oauth_states"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "tenant_configs",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column(
            "tenant_id",
            sa.String(length=36),
            sa.ForeignKey("tenants.id"),
            nullable=False,
            unique=True,
            index=True,
        ),
        sa.Column("azure_tenant_id", sa.String(length=64), nullable=True),
        sa.Column("azure_client_id", sa.String(length=64), nullable=True),
        sa.Column("azure_client_secret_ref", sa.String(length=255), nullable=True),
        sa.Column("azure_public_client", sa.Boolean, nullable=False, server_default=sa.false()),
        sa.Column(
            "updated_by_user_id",
            sa.String(length=36),
            sa.ForeignKey("users.id"),
            nullable=True,
        ),
        sa.Column("last_rotated_at", sa.DateTime, nullable=True),
        sa.Column("created_at", sa.DateTime, nullable=False),
        sa.Column("updated_at", sa.DateTime, nullable=False),
    )


def downgrade() -> None:
    op.drop_table("tenant_configs")
