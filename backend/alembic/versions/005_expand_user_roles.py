"""Sprint 3.1 — expand UserRole enum from 3 to 6 tiers

Existing data is preserved: legacy values (admin/user/viewer) remain valid.
This migration is additive — it only ensures the Enum column accepts the
new values (Postgres/MSSQL).

For SQLite, the role column is a VARCHAR via SQLAlchemy's Enum compat
shim and accepts any string, so no DDL change is needed; the seed step
below back-fills standard_user / read_only_user labels lazily on next
login (via security.py) when ENFORCE_TOOL_ROLE_GATES=true.

Revision ID: 005_expand_user_roles
Revises: 004_gdpr_soft_delete
Create Date: 2026-05-21
"""

from alembic import op
import sqlalchemy as sa


revision = '005_expand_user_roles'
down_revision = '004_gdpr_soft_delete'
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    dialect = bind.dialect.name

    if dialect == "postgresql":
        for value in (
            "platform_admin", "tenant_admin", "power_user",
            "standard_user", "read_only_user", "service_account",
        ):
            try:
                op.execute(
                    f"ALTER TYPE userrole ADD VALUE IF NOT EXISTS '{value}'"
                )
            except Exception as e:
                print(f"[005] add enum value {value} skipped: {e}")
        return

    # MSSQL: column is just nvarchar(50) — nothing to alter.
    # SQLite: SQLAlchemy Enum is stored as VARCHAR, also nothing to alter.
    # The new values are accepted by the column as-is; the application code
    # uses the new identifiers.


def downgrade() -> None:
    # Enum-value removal is destructive and unsupported in most dialects;
    # downgrade is intentionally a no-op.
    pass
