"""merge heads

Revision ID: bd62e1999341
Revises: 3b1e0f4a2d9c, e425a99e7480
Create Date: 2026-05-16 00:06:52.793155

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'bd62e1999341'
down_revision: Union[str, Sequence[str], None] = ('3b1e0f4a2d9c', 'e425a99e7480')
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    pass


def downgrade() -> None:
    """Downgrade schema."""
    pass
