"""add doctor role and phone_number to users

Revision ID: c9f2a1b3d4e5
Revises: b705fe90560f
Create Date: 2026-05-24 00:00:00.000000

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "c9f2a1b3d4e5"
down_revision: Union[str, Sequence[str], None] = "b705fe90560f"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Add doctor to the userrole enum — cannot run inside a transaction
    op.execute("ALTER TYPE userrole ADD VALUE IF NOT EXISTS 'doctor'")

    # Add phone_number column
    op.add_column("users", sa.Column("phone_number", sa.String(), nullable=True))


def downgrade() -> None:
    op.drop_column("users", "phone_number")
    # NOTE: Postgres does not support removing enum values.
    # To fully revert: recreate the enum type without 'doctor' and cast the column.