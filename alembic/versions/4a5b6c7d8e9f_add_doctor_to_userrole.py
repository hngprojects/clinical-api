"""add_doctor_to_userrole

Revision ID: 4a5b6c7d8e9f
Revises: 2b6d8f0a4c9e
Create Date: 2026-07-23 10:58:00.000000

"""

from typing import Sequence, Union

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "4a5b6c7d8e9f"
down_revision: Union[str, Sequence[str], None] = "2b6d8f0a4c9e"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
	with op.get_context().autocommit_block():
		op.execute("ALTER TYPE userrole ADD VALUE IF NOT EXISTS 'doctor'")


def downgrade() -> None:
	pass
