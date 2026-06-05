"""add_partial_unique_index_on_lab_result_id

Revision ID: a1efa2ce5f0d
Revises: ad01e5ccd53a
Create Date: 2026-06-03 10:18:36.983337

"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'a1efa2ce5f0d'
down_revision: Union[str, Sequence[str], None] = 'ad01e5ccd53a'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    # Drop the non-unique index — the unique partial index below covers it.
    op.drop_index('ix_chat_lab_result_id', table_name='chat')
    # Add a partial unique index: only non-null lab_result_id values are unique.
    op.create_index(
        'ix_chat_lab_result_id_unique', 'chat', ['lab_result_id'],
        unique=True,
        postgresql_where=sa.text('lab_result_id IS NOT NULL'),
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index('ix_chat_lab_result_id_unique', table_name='chat')
    op.create_index('ix_chat_lab_result_id', 'chat', ['lab_result_id'], unique=False)