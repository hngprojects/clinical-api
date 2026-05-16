"""add guest_sessions table

Revision ID: af630961c482
Revises: 3b1e0f4a2d9c
Create Date: 2026-05-15 13:58:33.470858

"""
from typing import Sequence, Union

from alembic import op

# revision identifiers, used by Alembic.
revision: str = 'af630961c482'
down_revision: Union[str, Sequence[str], None] = '3b1e0f4a2d9c'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.execute("""
        CREATE TABLE IF NOT EXISTS guest_sessions (
            id UUID NOT NULL,
            session_id VARCHAR(36) NOT NULL,
            expires_at TIMESTAMP WITH TIME ZONE NOT NULL,
            created_at TIMESTAMP WITH TIME ZONE NOT NULL,
            last_active_at TIMESTAMP WITH TIME ZONE NOT NULL,
            PRIMARY KEY (id)
        )
    """)
    op.execute("""
        CREATE UNIQUE INDEX IF NOT EXISTS ix_guest_sessions_session_id
        ON guest_sessions (session_id)
    """)


def downgrade() -> None:
    """Downgrade schema."""
    op.execute("DROP INDEX IF EXISTS ix_guest_sessions_session_id")
    op.execute("DROP TABLE IF EXISTS guest_sessions")