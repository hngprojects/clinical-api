"""add avatar_url to users

Revision ID: 1a2b3c4d5e6f
Revises: a1efa2ce5f0d
Create Date: 2026-06-06 10:41:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "1a2b3c4d5e6f"
down_revision: Union[str, Sequence[str], None] = "a1efa2ce5f0d"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
	op.add_column("users", sa.Column("avatar_url", sa.String(), nullable=True))


def downgrade() -> None:
	op.drop_column("users", "avatar_url")