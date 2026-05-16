"""initial

Revision ID: a8598ffe37d5
Revises:
Create Date: 2026-05-14 10:32:10.609127

"""

from typing import Sequence, Union

from alembic import op

import app.models  # noqa: F401 — register all models with metadata
from app.models.base import Base

# revision identifiers, used by Alembic.
revision: str = "a8598ffe37d5"
down_revision: Union[str, Sequence[str], None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
	"""Create the full schema from SQLAlchemy model metadata."""
	bind = op.get_bind()
	Base.metadata.create_all(bind)


def downgrade() -> None:
	"""Drop all application tables."""
	bind = op.get_bind()
	Base.metadata.drop_all(bind)
