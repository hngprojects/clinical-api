"""Add reset_password to otppurpose enum

Revision ID: 8b7d3f0a9d21
Revises: da75221c0906
Create Date: 2026-05-20 13:40:00.000000

"""
from typing import Sequence, Union

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "8b7d3f0a9d21"
down_revision: Union[str, Sequence[str], None] = "da75221c0906"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
	op.execute("ALTER TYPE otppurpose ADD VALUE IF NOT EXISTS 'reset_password'")


def downgrade() -> None:
	# PostgreSQL does not support removing enum values in-place.
	# Downgrades can leave the extra value in place safely.
	pass