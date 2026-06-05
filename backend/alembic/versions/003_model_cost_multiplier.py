"""Add cost_multiplier column to model_rankings + backfill defaults

Revision ID: 003_model_cost_multiplier
Revises: 002_agent_memory
Create Date: 2026-04-24
"""

from alembic import op
import sqlalchemy as sa


revision = '003_model_cost_multiplier'
down_revision = '002_agent_memory'
branch_labels = None
depends_on = None


# Backfill defaults so existing prod rows show sensible multipliers
# without requiring an admin to touch every row.
_DEFAULT_MULTIPLIERS = {
    "gpt-5.2-chat":      7.5,
    "gpt-4.1":           3.0,
    "gpt-4o":            3.0,
    "gpt-4o-mini":       1.0,
    "kimi-k2.5":         2.0,
    "mistral-large-3":   2.0,
    "grok-3-mini":       1.0,
    "llama-4-maverick":  1.0,
    "llama-4-maverick-17b-128e-instruct-fp8": 1.0,
    "gemini-2.0-flash":  1.0,
    "gemini-1.5-pro":    4.0,
    "gemini-1.5-flash":  1.0,
    "claude-opus-4-6":   15.0,
    "claude-sonnet-4-6": 5.0,
    "claude-haiku-4-5":  1.0,
}


def upgrade() -> None:
    op.add_column(
        'model_rankings',
        sa.Column(
            'cost_multiplier',
            sa.Float(),
            nullable=False,
            server_default='1.0',
        ),
    )
    # Backfill known models with their canonical multipliers
    conn = op.get_bind()
    for model_id, mult in _DEFAULT_MULTIPLIERS.items():
        conn.execute(
            sa.text(
                "UPDATE model_rankings SET cost_multiplier = :m "
                "WHERE model_id = :mid"
            ),
            {"m": mult, "mid": model_id},
        )


def downgrade() -> None:
    op.drop_column('model_rankings', 'cost_multiplier')
