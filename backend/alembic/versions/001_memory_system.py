"""Add memory tables for three-layer memory system

Revision ID: 001_memory_system
Revises:
Create Date: 2025-01-21
"""

from alembic import op
import sqlalchemy as sa

# revision identifiers
revision = '001_memory_system'
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Create MemoryType enum (handled inline via String for cross-DB compat)

    # Table: user_memories (Layer 1: Long-term memory)
    op.create_table(
        'user_memories',
        sa.Column('id', sa.String(36), primary_key=True),
        sa.Column(
            'user_id', sa.String(36),
            sa.ForeignKey('users.id', ondelete='CASCADE'), nullable=False
        ),
        sa.Column(
            'memory_type', sa.String(20), nullable=False, default='fact'
        ),
        sa.Column('content', sa.Text, nullable=False),
        sa.Column('category', sa.String(50), nullable=True),
        sa.Column(
            'source_conversation_id', sa.String(36),
            sa.ForeignKey('conversations.id', ondelete='SET NULL'),
            nullable=True
        ),
        sa.Column('relevance_score', sa.Integer, nullable=False, default=5),
        sa.Column('usage_count', sa.Integer, nullable=False, default=0),
        sa.Column('is_active', sa.Boolean, nullable=False, default=True),
        sa.Column(
            'profile_scope', sa.String(20), nullable=False, default='global'
        ),
        sa.Column('tenant_id', sa.String(36), nullable=True),
        sa.Column(
            'created_at', sa.DateTime, nullable=False,
            server_default=sa.func.now()
        ),
        sa.Column(
            'updated_at', sa.DateTime, nullable=False,
            server_default=sa.func.now(), onupdate=sa.func.now()
        ),
    )

    # Indexes for user_memories
    op.create_index(
        'ix_user_memories_user_id', 'user_memories', ['user_id']
    )
    op.create_index(
        'ix_user_memories_type', 'user_memories', ['memory_type']
    )
    op.create_index(
        'ix_user_memories_category', 'user_memories', ['category']
    )
    op.create_index(
        'ix_user_memories_profile', 'user_memories',
        ['user_id', 'profile_scope', 'tenant_id']
    )
    op.create_index(
        'ix_user_memories_active', 'user_memories', ['user_id', 'is_active']
    )

    # Table: session_memories (Layer 2: Session memory)
    op.create_table(
        'session_memories',
        sa.Column('id', sa.String(36), primary_key=True),
        sa.Column(
            'conversation_id', sa.String(36),
            sa.ForeignKey('conversations.id', ondelete='CASCADE'),
            nullable=False, unique=True
        ),
        sa.Column(
            'user_id', sa.String(36),
            sa.ForeignKey('users.id', ondelete='CASCADE'), nullable=False
        ),
        sa.Column('summary', sa.Text, nullable=False),
        sa.Column('key_facts', sa.Text, nullable=True),
        sa.Column('goals', sa.Text, nullable=True),
        sa.Column('entities', sa.Text, nullable=True),
        sa.Column('token_count', sa.Integer, nullable=False, default=0),
        sa.Column('last_message_id', sa.String(36), nullable=True),
        sa.Column('message_count', sa.Integer, nullable=False, default=0),
        sa.Column(
            'profile_mode', sa.String(20), nullable=False, default='personal'
        ),
        sa.Column('tenant_id', sa.String(36), nullable=True),
        sa.Column(
            'created_at', sa.DateTime, nullable=False,
            server_default=sa.func.now()
        ),
        sa.Column(
            'updated_at', sa.DateTime, nullable=False,
            server_default=sa.func.now(), onupdate=sa.func.now()
        ),
        sa.Column('expires_at', sa.DateTime, nullable=False),
    )

    # Indexes for session_memories
    op.create_index(
        'ix_session_memories_conversation_id', 'session_memories',
        ['conversation_id']
    )
    op.create_index(
        'ix_session_memories_user_id', 'session_memories', ['user_id']
    )
    op.create_index(
        'ix_session_memories_expires', 'session_memories', ['expires_at']
    )
    op.create_index(
        'ix_session_memories_profile', 'session_memories',
        ['user_id', 'profile_mode', 'tenant_id']
    )


def downgrade() -> None:
    # Drop session_memories
    op.drop_index('ix_session_memories_profile', 'session_memories')
    op.drop_index('ix_session_memories_expires', 'session_memories')
    op.drop_index('ix_session_memories_user_id', 'session_memories')
    op.drop_index(
        'ix_session_memories_conversation_id', 'session_memories'
    )
    op.drop_table('session_memories')

    # Drop user_memories
    op.drop_index('ix_user_memories_active', 'user_memories')
    op.drop_index('ix_user_memories_profile', 'user_memories')
    op.drop_index('ix_user_memories_category', 'user_memories')
    op.drop_index('ix_user_memories_type', 'user_memories')
    op.drop_index('ix_user_memories_user_id', 'user_memories')
    op.drop_table('user_memories')
