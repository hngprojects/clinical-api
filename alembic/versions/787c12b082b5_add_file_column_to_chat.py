"""add_file_column_to_chat
Revision ID: 787c12b082b5
Revises: 3f2a9f6e7b14
Create Date: 2026-06-03 07:33:10.102770
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = '787c12b082b5'
down_revision: Union[str, Sequence[str], None] = '3f2a9f6e7b14'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""

    # Run Postgres enum change in autocommit mode
    with op.get_context().autocommit_block():
        op.execute(
            "ALTER TYPE sendertype ADD VALUE IF NOT EXISTS 'file'"
        )

    # Add file column
    op.add_column(
        "chat",
        sa.Column(
            "file",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=True,
        ),
    )

    # Update FK constraint
    op.drop_constraint(
        "notification_medical_case_id_fkey",
        "notification",
        type_="foreignkey",
    )

    op.create_foreign_key(
        "notification_medical_case_id_fkey_cascade",
        "notification",
        "medical_cases",
        ["medical_case_id"],
        ["id"],
        ondelete="CASCADE",
    )

def downgrade() -> None:
    """Downgrade schema."""
    # Drop the file column
    op.drop_column('chat', 'file')

    # Note: We cannot remove 'file' from the sendertype enum in PostgreSQL in a
    # transaction-safe way.  The enum value will remain but will not be used.

    op.drop_constraint('notification_medical_case_id_fkey_cascade', 'notification', type_='foreignkey')
    op.create_foreign_key(
        'notification_medical_case_id_fkey',
        'notification', 'medical_cases',
        ['medical_case_id'], ['id'],
        ondelete='SET NULL',
    )