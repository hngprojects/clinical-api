"""add pending_email and email_change_token to users

Revision ID: 3b1e0f4a2d9c
Revises: a8598ffe37d5
Create Date: 2026-05-15 00:00:00.000000

The initial revision was committed empty; on databases where it already ran,
this revision bootstraps the schema and adds email-change columns when needed.
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect

import app.models  # noqa: F401 — register all models with metadata
from app.models.base import Base

# revision identifiers, used by Alembic.
revision: str = "3b1e0f4a2d9c"
down_revision: Union[str, Sequence[str], None] = "a8598ffe37d5"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
	bind = op.get_bind()
	insp = inspect(bind)

	if not insp.has_table("users"):
		Base.metadata.create_all(bind)
		return

	columns = {column["name"] for column in insp.get_columns("users")}
	if "pending_email" not in columns:
		op.add_column("users", sa.Column("pending_email", sa.String(), nullable=True))
	if "email_change_token" not in columns:
		op.add_column("users", sa.Column("email_change_token", sa.String(), nullable=True))


def downgrade() -> None:
	bind = op.get_bind()
	insp = inspect(bind)

	if not insp.has_table("users"):
		return

	columns = {column["name"] for column in insp.get_columns("users")}
	if "email_change_token" in columns:
		op.drop_column("users", "email_change_token")
	if "pending_email" in columns:
		op.drop_column("users", "pending_email")
