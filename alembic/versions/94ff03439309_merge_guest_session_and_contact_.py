"""merge guest session and contact messages heads

Revision ID: 94ff03439309
Revises: 5e0a4aea1afd, e425a99e7480
Create Date: 2026-05-15 23:53:30.762189

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '94ff03439309'
down_revision: Union[str, Sequence[str], None] = ('5e0a4aea1afd', 'e425a99e7480')
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    pass


def downgrade() -> None:
    """Downgrade schema."""
    pass
