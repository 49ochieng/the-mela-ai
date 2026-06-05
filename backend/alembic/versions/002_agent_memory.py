"""Add agent_memory_items table for the Agent Memory feature

Revision ID: 002_agent_memory
Revises: 001_memory_system
Create Date: 2025-01-22
"""

from alembic import op
import sqlalchemy as sa


# revision identifiers
revision = '002_agent_memory'
down_revision = '001_memory_system'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        'agent_memory_items',
        sa.Column('id', sa.String(36), primary_key=True),
        sa.Column(
            'user_id', sa.String(36),
            sa.ForeignKey('users.id', ondelete='CASCADE'),
            nullable=False,
        ),
        sa.Column('tenant_id', sa.String(36), nullable=True),
        # 'personal' | 'workspace' | 'tenant'
        sa.Column('scope', sa.String(20), nullable=False, server_default='personal'),
        # 'knowledge' | 'template' | 'brand' | 'policy' | 'demo'
        sa.Column('tag', sa.String(20), nullable=False, server_default='knowledge'),
        # 'upload' | 'web' | 'sharepoint' | 'onedrive'
        sa.Column('source_type', sa.String(20), nullable=False),
        sa.Column('source_id', sa.String(500), nullable=False),
        sa.Column('title', sa.String(500), nullable=False),
        sa.Column('url', sa.String(2000), nullable=True),
        sa.Column('blob_url', sa.String(2000), nullable=True),
        sa.Column('file_type', sa.String(50), nullable=True),
        sa.Column('file_size', sa.Integer, nullable=True),
        sa.Column('content_hash', sa.String(64), nullable=True),
        # 'pending' | 'parsing' | 'crawling' | 'embedding' | 'ready' | 'failed'
        sa.Column('status', sa.String(20), nullable=False, server_default='pending'),
        sa.Column('error_message', sa.Text, nullable=True),
        sa.Column('chunk_count', sa.Integer, nullable=False, server_default='0'),
        sa.Column('page_count', sa.Integer, nullable=False, server_default='0'),
        sa.Column('template_schema_json', sa.JSON, nullable=True),
        sa.Column('session_disabled', sa.JSON, nullable=True),
        sa.Column('last_synced_at', sa.DateTime, nullable=True),
        sa.Column(
            'created_at', sa.DateTime, nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column(
            'updated_at', sa.DateTime, nullable=False,
            server_default=sa.func.now(),
        ),
        sa.UniqueConstraint(
            'user_id', 'content_hash',
            name='uq_agent_memory_user_hash',
        ),
    )

    op.create_index(
        'ix_agent_memory_owner', 'agent_memory_items',
        ['user_id', 'scope', 'status'],
    )
    op.create_index(
        'ix_agent_memory_tenant', 'agent_memory_items',
        ['tenant_id', 'scope', 'tag'],
    )
    op.create_index(
        'ix_agent_memory_status', 'agent_memory_items', ['status'],
    )


def downgrade() -> None:
    op.drop_index('ix_agent_memory_status', table_name='agent_memory_items')
    op.drop_index('ix_agent_memory_tenant', table_name='agent_memory_items')
    op.drop_index('ix_agent_memory_owner', table_name='agent_memory_items')
    op.drop_table('agent_memory_items')
