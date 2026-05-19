"""merge multiple heads

Revision ID: c9a4408f408d
Revises: 66defcb90b0f, f3a2c1d8e9b0, f8c3d21a4b90
Create Date: 2026-05-19 15:32:18.025741

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'c9a4408f408d'
down_revision: Union[str, Sequence[str], None] = ('66defcb90b0f', 'f3a2c1d8e9b0', 'f8c3d21a4b90')
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    pass


def downgrade() -> None:
    """Downgrade schema."""
    pass
