"""Add alert_events table

Revision ID: 006_alert_events
Revises: 005_expand_user_roles
Create Date: 2026-05-22
"""

from alembic import op
import sqlalchemy as sa


revision = '006_alert_events'
down_revision = '005_expand_user_roles'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        'alert_events',
        sa.Column('id', sa.String(length=36), primary_key=True),
        sa.Column('created_at', sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.Column('incident_id', sa.String(length=36), nullable=False),
        sa.Column('severity', sa.String(length=20), nullable=False, server_default='critical'),
        sa.Column('code', sa.String(length=80), nullable=False, server_default=''),
        sa.Column('title', sa.String(length=300), nullable=False, server_default=''),
        sa.Column('route', sa.String(length=500), nullable=True),
        sa.Column('tenant_id', sa.String(length=36), nullable=True),
        sa.Column('channels_attempted', sa.JSON(), nullable=False),
        sa.Column('ai_triage_confidence', sa.Float(), nullable=True),
    )
    op.create_index('ix_alert_events_incident_id', 'alert_events', ['incident_id'])
    op.create_index('ix_alert_events_created_at', 'alert_events', ['created_at'])
    op.create_index('ix_alert_events_severity', 'alert_events', ['severity'])
    op.create_index('ix_alert_events_code', 'alert_events', ['code'])


def downgrade() -> None:
    op.drop_index('ix_alert_events_code', table_name='alert_events')
    op.drop_index('ix_alert_events_severity', table_name='alert_events')
    op.drop_index('ix_alert_events_created_at', table_name='alert_events')
    op.drop_index('ix_alert_events_incident_id', table_name='alert_events')
    op.drop_table('alert_events')
