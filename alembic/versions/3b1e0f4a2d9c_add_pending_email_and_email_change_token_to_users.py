"""add pending_email and email_change_token to users

Revision ID: 3b1e0f4a2d9c
Revises: a8598ffe37d5
Create Date: 2026-05-15 00:00:00.000000
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "3b1e0f4a2d9c"
down_revision: Union[str, Sequence[str], None] = "a8598ffe37d5"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
	op.add_column("users", sa.Column("pending_email", sa.String(), nullable=True))
	op.add_column("users", sa.Column("email_change_token", sa.String(), nullable=True))


def downgrade() -> None:
	op.drop_column("users", "email_change_token")
	op.drop_column("users", "pending_email")
