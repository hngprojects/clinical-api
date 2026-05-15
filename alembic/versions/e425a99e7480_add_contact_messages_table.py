"""add contact_messages table

Revision ID: e425a99e7480
Revises: 3b1e0f4a2d9c
Create Date: 2026-05-15 15:44:40.692156

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect

revision: str = 'e425a99e7480'
down_revision: Union[str, Sequence[str], None] = '3b1e0f4a2d9c'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    bind = op.get_bind()
    insp = inspect(bind)
    if not insp.has_table("contact_messages"):
        op.create_table(
            'contact_messages',
            sa.Column('id', sa.UUID(), nullable=False),
            sa.Column('full_name', sa.String(length=150), nullable=False),
            sa.Column('email', sa.String(length=255), nullable=False),
            sa.Column('message', sa.Text(), nullable=False),
            sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
            sa.PrimaryKeyConstraint('id')
        )


def downgrade() -> None:
    bind = op.get_bind()
    insp = inspect(bind)
    if insp.has_table("contact_messages"):
        op.drop_table('contact_messages')