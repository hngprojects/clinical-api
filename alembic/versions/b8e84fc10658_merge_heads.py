"""merge heads

Revision ID: b8e84fc10658
Revises: cd5d4fabf8c6, e425a99e7480
Create Date: 2026-05-16 03:05:26.355759

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'b8e84fc10658'
down_revision: Union[str, Sequence[str], None] = ('cd5d4fabf8c6', 'e425a99e7480')
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    pass


def downgrade() -> None:
    """Downgrade schema."""
    pass
