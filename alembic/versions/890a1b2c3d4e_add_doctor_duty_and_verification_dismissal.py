"""add doctor duty status and verification banner dismissal to users table

Revision ID: 890a1b2c3d4e
Revises: 6f7a8b9c0d1e, a1efa2ce5f0d
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "890a1b2c3d4e"
down_revision: Union[str, Sequence[str], None] = ("6f7a8b9c0d1e", "a1efa2ce5f0d")
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
	op.add_column(
		"users",
		sa.Column("is_on_duty", sa.Boolean(), nullable=False, server_default=sa.text("false")),
	)
	op.add_column(
		"users",
		sa.Column("on_duty_since", sa.DateTime(timezone=True), nullable=True),
	)
	op.add_column(
		"users",
		sa.Column("is_verification_dismissed", sa.Boolean(), nullable=False, server_default=sa.text("false")),
	)
	op.alter_column("users", "is_on_duty", server_default=None)
	op.alter_column("users", "is_verification_dismissed", server_default=None)


def downgrade() -> None:
	op.drop_column("users", "is_verification_dismissed")
	op.drop_column("users", "on_duty_since")
	op.drop_column("users", "is_on_duty")
