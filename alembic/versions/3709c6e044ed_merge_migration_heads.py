"""merge migration heads

Revision ID: 3709c6e044ed
Revises: b705fe90560f, e425a99e7480
Create Date: 2026-05-22 23:51:58.653162

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '3709c6e044ed'
down_revision: Union[str, Sequence[str], None] = ('b705fe90560f', 'e425a99e7480')
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    pass


def downgrade() -> None:
    """Downgrade schema."""
    pass
