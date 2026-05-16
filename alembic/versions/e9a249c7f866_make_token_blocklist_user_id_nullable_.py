"""make_token_blocklist_user_id_nullable_set_null_on_delete

Revision ID: e9a249c7f866
Revises: e425a99e7480
Create Date: 2026-05-16 09:13:45.795240

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = 'e9a249c7f866'
down_revision: Union[str, Sequence[str], None] = 'e425a99e7480'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.alter_column('token_blocklist', 'user_id', existing_type=sa.UUID(), nullable=True)
    op.drop_constraint('token_blocklist_user_id_fkey', 'token_blocklist', type_='foreignkey')
    op.create_foreign_key(None, 'token_blocklist', 'users', ['user_id'], ['id'], ondelete='SET NULL')


def downgrade() -> None:
    op.drop_constraint(None, 'token_blocklist', type_='foreignkey')
    op.create_foreign_key('token_blocklist_user_id_fkey', 'token_blocklist', 'users', ['user_id'], ['id'], ondelete='CASCADE')
    op.alter_column('token_blocklist', 'user_id', existing_type=sa.UUID(), nullable=False)
