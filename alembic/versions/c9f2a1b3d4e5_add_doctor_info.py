"""add doctor role, phone number and doctor profiles table

Revision ID: c9f2a1b3d4e5
Revises: b705fe90560f
Create Date: 2026-05-24 00:00:00.000000

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "c9f2a1b3d4e5"
down_revision: Union[str, Sequence[str], None] = "b705fe90560f"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # 1. Add doctor to userrole enum safely
    op.execute("ALTER TYPE userrole ADD VALUE IF NOT EXISTS 'doctor'")

    # 2. Add phone_number to users safely
    op.execute("ALTER TABLE users ADD COLUMN IF NOT EXISTS phone_number VARCHAR")

    # 3. Create doctorverificationstatus enum safely via PL/pgSQL
    op.execute(
        "DO $$ BEGIN "
        "IF NOT EXISTS (SELECT 1 FROM pg_type WHERE typname = 'doctorverificationstatus') THEN "
        "CREATE TYPE doctorverificationstatus AS ENUM ('incomplete', 'pending_review', 'verified', 'rejected'); "
        "END IF; "
        "END $$;"
    )

    # 4. Create doctor_profiles table
    op.create_table(
        "doctor_profiles",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("user_id", sa.UUID(), nullable=False),
        sa.Column("specialization", sa.String(), nullable=True),
        sa.Column("years_of_experience", sa.Integer(), nullable=True),
        sa.Column("hospital", sa.String(), nullable=True),
        sa.Column("passport_photo_url", sa.String(), nullable=True),
        sa.Column("mdcn_license_number", sa.String(), nullable=True),
        sa.Column("nin", sa.String(), nullable=True),
        sa.Column("medical_license_url", sa.String(), nullable=True),
        sa.Column(
            "verification_status",
            sa.Enum(
                "incomplete", "pending_review", "verified", "rejected",
                name="doctorverificationstatus",
                create_type=False 
            ),
            nullable=False,
        ),
        sa.Column("rejection_reason", sa.Text(), nullable=True),
        sa.Column("submitted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("user_id"),
    )
    op.create_index("ix_doctor_profiles_user_id", "doctor_profiles", ["user_id"])


def downgrade() -> None:
    op.drop_index("ix_doctor_profiles_user_id", table_name="doctor_profiles")
    op.drop_table("doctor_profiles")
    op.execute("DROP TYPE IF EXISTS doctorverificationstatus")
    op.drop_column("users", "phone_number")