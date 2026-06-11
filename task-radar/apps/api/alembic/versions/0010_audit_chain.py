"""audit log hash chain + forensic columns

Revision ID: 0010_audit_chain
Revises: 0009_tenant_configs
Create Date: 2026-05-11
"""
from __future__ import annotations

from typing import Union

import sqlalchemy as sa
from alembic import op

revision: str = "0010_audit_chain"
down_revision: Union[str, None] = "0009_tenant_configs"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("audit_logs") as b:
        b.add_column(sa.Column("prev_hash", sa.String(length=64), nullable=True))
        b.add_column(sa.Column("entry_hash", sa.String(length=64), nullable=True))
        b.add_column(sa.Column("seq", sa.Integer, nullable=True))
        b.add_column(sa.Column("ip", sa.String(length=45), nullable=True))
        b.add_column(sa.Column("user_agent", sa.String(length=255), nullable=True))
        b.add_column(sa.Column("request_id", sa.String(length=36), nullable=True))
    op.create_index("ix_audit_logs_entry_hash", "audit_logs", ["entry_hash"])
    op.create_index("ix_audit_logs_seq", "audit_logs", ["seq"])
    op.create_index("ix_audit_logs_request_id", "audit_logs", ["request_id"])


def downgrade() -> None:
    op.drop_index("ix_audit_logs_request_id", table_name="audit_logs")
    op.drop_index("ix_audit_logs_seq", table_name="audit_logs")
    op.drop_index("ix_audit_logs_entry_hash", table_name="audit_logs")
    with op.batch_alter_table("audit_logs") as b:
        b.drop_column("request_id")
        b.drop_column("user_agent")
        b.drop_column("ip")
        b.drop_column("seq")
        b.drop_column("entry_hash")
        b.drop_column("prev_hash")
