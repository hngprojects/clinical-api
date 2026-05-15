"""merge heads

Revision ID: 5e0a4aea1afd
Revises: af630961c482
Create Date: 2026-05-15 23:17:53.442670

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '5e0a4aea1afd'
down_revision: Union[str, Sequence[str], None] = 'af630961c482'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    pass


def downgrade() -> None:
    """Downgrade schema."""
    pass
