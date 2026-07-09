"""add first_name to waitlist

Revision ID: 2b6d8f0a4c9e
Revises: 1a2b3c4d5e6f
Create Date: 2026-07-09 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "2b6d8f0a4c9e"
down_revision: Union[str, Sequence[str], None] = "1a2b3c4d5e6f"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
	op.add_column("waitlist", sa.Column("first_name", sa.String(), nullable=True))


def downgrade() -> None:
	op.drop_column("waitlist", "first_name")
