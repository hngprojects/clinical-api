"""add doctor case assignment lifecycle

Revision ID: 6f7a8b9c0d1e
Revises: 3f2a9f6e7b14
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision: str = "6f7a8b9c0d1e"
down_revision: Union[str, Sequence[str], None] = "5c6d7e8f9a0b"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
	doctor_status = postgresql.ENUM("pending", "accepted", "declined", "cancelled", name="doctorcasestatus")
	doctor_status.create(op.get_bind(), checkfirst=True)
	op.add_column("medical_cases", sa.Column("doctor_id", postgresql.UUID(as_uuid=True), nullable=True))
	op.add_column("medical_cases", sa.Column("doctor_case_status", doctor_status, nullable=True))
	op.add_column(
		"medical_cases",
		sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True, server_default=sa.text("CURRENT_TIMESTAMP")),
	)
	op.create_foreign_key("fk_medical_cases_doctor_id_users", "medical_cases", "users", ["doctor_id"], ["id"], ondelete="SET NULL")
	op.create_index(
		"ix_medical_cases_doctor_case_status_created",
		"medical_cases",
		["doctor_id", "doctor_case_status", "created_at"],
		unique=False,
	)
	op.create_index(
		"ix_medical_cases_doctor_case_status_updated",
		"medical_cases",
		["doctor_id", "doctor_case_status", "updated_at"],
		unique=False,
	)
	op.alter_column("medical_cases", "updated_at", nullable=False, server_default=None)


def downgrade() -> None:
	op.drop_index("ix_medical_cases_doctor_case_status_updated", table_name="medical_cases")
	op.drop_index("ix_medical_cases_doctor_case_status_created", table_name="medical_cases")
	op.drop_constraint("fk_medical_cases_doctor_id_users", "medical_cases", type_="foreignkey")
	op.drop_column("medical_cases", "updated_at")
	op.drop_column("medical_cases", "doctor_case_status")
	op.drop_column("medical_cases", "doctor_id")
	postgresql.ENUM(name="doctorcasestatus").drop(op.get_bind(), checkfirst=True)
