"""add pipeline_audit_log table

Revision ID: f3a2c1d8e9b0
Revises: e425a99e7480
Create Date: 2026-05-18 00:00:00.000000

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy import inspect

revision: str = "f3a2c1d8e9b0"
down_revision: Union[str, Sequence[str], None] = "e425a99e7480"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    bind = op.get_bind()
    insp = inspect(bind)
    if not insp.has_table("pipeline_audit_logs"):
        op.create_table(
            "pipeline_audit_logs",
            sa.Column("id", sa.UUID(), nullable=False),
            sa.Column("lab_result_id", sa.UUID(), nullable=False),
            sa.Column("event", sa.String(), nullable=False),
            sa.Column("status_before", sa.String(), nullable=True),
            sa.Column("status_after", sa.String(), nullable=True),
            sa.Column("provider", sa.String(), nullable=True),
            sa.Column("duration_ms", sa.Integer(), nullable=True),
            sa.Column("attempt", sa.Integer(), nullable=True),
            sa.Column("error", sa.Text(), nullable=True),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.ForeignKeyConstraint(["lab_result_id"], ["lab_results.id"], ondelete="CASCADE"),
            sa.PrimaryKeyConstraint("id"),
        )
        op.create_index("ix_pipeline_audit_logs_lab_result_id", "pipeline_audit_logs", ["lab_result_id"])


def downgrade() -> None:
    bind = op.get_bind()
    insp = inspect(bind)
    if insp.has_table("pipeline_audit_logs"):
        op.drop_index("ix_pipeline_audit_logs_lab_result_id", table_name="pipeline_audit_logs")
        op.drop_table("pipeline_audit_logs")