"""GDPR Sprint 2 — add deleted_at column for soft-delete

Adds a nullable deleted_at DateTime column to: users, conversations,
messages, documents, projects. Migration is purely additive (NULL default),
no data backfill required.

Revision ID: 004_gdpr_soft_delete
Revises: 003_model_cost_multiplier
Create Date: 2026-05-21
"""

from alembic import op
import sqlalchemy as sa


revision = '004_gdpr_soft_delete'
down_revision = '003_model_cost_multiplier'
branch_labels = None
depends_on = None


_TABLES = ["users", "conversations", "messages", "documents", "projects"]


def upgrade() -> None:
    for table in _TABLES:
        try:
            op.add_column(
                table,
                sa.Column("deleted_at", sa.DateTime(), nullable=True),
            )
            op.create_index(
                f"ix_{table}_deleted_at",
                table,
                ["deleted_at"],
            )
        except Exception as e:
            # Table may not exist in legacy schemas — log and continue so
            # the migration is resilient on partially-bootstrapped DBs.
            print(f"[004_gdpr_soft_delete] skipping {table}: {e}")


def downgrade() -> None:
    for table in _TABLES:
        try:
            op.drop_index(f"ix_{table}_deleted_at", table_name=table)
            op.drop_column(table, "deleted_at")
        except Exception as e:
            print(f"[004_gdpr_soft_delete] skipping {table} downgrade: {e}")
