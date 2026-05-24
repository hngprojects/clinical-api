"""Add phone_number column and doctor role to userrole enum

Revision ID: 20260524_add_phone_and_doctor_role
Revises: b705fe90560f
Create Date: 2026-05-24 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = "20260524_add_phone_and_doctor_role"
down_revision: Union[str, Sequence[str], None] = "b705fe90560f"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # add phone_number column
    op.add_column("users", sa.Column("phone_number", sa.String(), nullable=True))

    # add "doctor" to existing userrole enum type
    try:
        op.execute("ALTER TYPE userrole ADD VALUE 'doctor'")
    except Exception:
        # if the value already exists or the DB doesn't support IF NOT EXISTS,
        # ignore and continue — migrations should be idempotent in test environments
        pass


def downgrade() -> None:
    # remove phone_number column
    op.drop_column("users", "phone_number")

    # NOTE: Removing a value from a Postgres ENUM is non-trivial and not done here.
    # This downgrade leaves the enum value in place.
    return
