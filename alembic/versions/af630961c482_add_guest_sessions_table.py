"""add guest_sessions table

Revision ID: af630961c482
Revises: a8598ffe37d5
Create Date: 2026-05-15 13:58:33.470858

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = 'af630961c482'
down_revision: Union[str, Sequence[str], None] = 'a8598ffe37d5'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table('guest_sessions',
    sa.Column('id', sa.UUID(), nullable=False),
    sa.Column('session_id', sa.String(length=36), nullable=False),
    sa.Column('expires_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('last_active_at', sa.DateTime(timezone=True), nullable=False),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_guest_sessions_session_id'), 'guest_sessions', ['session_id'], unique=True)
    op.create_table('token_blocklist',
    sa.Column('id', sa.UUID(), nullable=False),
    sa.Column('jti', sa.String(length=36), nullable=False),
    sa.Column('user_id', sa.UUID(), nullable=False),
    sa.Column('expires_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.ForeignKeyConstraint(['user_id'], ['users.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index('ix_token_blocklist_expires_at', 'token_blocklist', ['expires_at'], unique=False)
    op.create_index(op.f('ix_token_blocklist_jti'), 'token_blocklist', ['jti'], unique=True)


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index(op.f('ix_token_blocklist_jti'), table_name='token_blocklist')
    op.drop_index('ix_token_blocklist_expires_at', table_name='token_blocklist')
    op.drop_table('token_blocklist')
    op.drop_index(op.f('ix_guest_sessions_session_id'), table_name='guest_sessions')
    op.drop_table('guest_sessions')