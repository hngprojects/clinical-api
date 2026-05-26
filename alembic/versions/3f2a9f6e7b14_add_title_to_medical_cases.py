"""add title to medical cases

Revision ID: 3f2a9f6e7b14
Revises: e9a249c7f866
Create Date: 2026-05-26 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "3f2a9f6e7b14"
down_revision: Union[str, Sequence[str], None] = "e9a249c7f866"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
	op.add_column("medical_cases", sa.Column("title", sa.String(), nullable=True))


def downgrade() -> None:
	op.drop_column("medical_cases", "title")