"""allow_same_email_and_google_id_across_roles

Revision ID: 5c6d7e8f9a0b
Revises: 4a5b6c7d8e9f
Create Date: 2026-07-23 15:08:00.000000

"""

from typing import Sequence, Union

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "5c6d7e8f9a0b"
down_revision: Union[str, Sequence[str], None] = "4a5b6c7d8e9f"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
	# Drop the single-column unique index on email
	op.drop_index("ix_users_email", table_name="users")

	# Drop the single-column unique constraint on google_id
	op.drop_constraint("users_google_id_key", "users", type_="unique")

	# Create composite unique index on (email, role) — allows one patient and one doctor per email
	op.create_index(
		"ix_users_email_role",
		"users",
		["email", "role"],
		unique=True,
	)

	# Re-create non-unique index on email for query performance
	op.create_index("ix_users_email", "users", ["email"], unique=False)

	# Create partial unique index on (google_id, role) for non-null google_ids only.
	# This allows the same Google account to be linked to both a patient and a doctor
	# account, while still preventing two doctors (or two patients) from sharing a
	# google_id.
	op.create_index(
		"ix_users_google_id_role",
		"users",
		["google_id", "role"],
		unique=True,
		postgresql_where=sa.text("google_id IS NOT NULL"),
	)


def downgrade() -> None:
	# Drop composite indexes
	op.drop_index("ix_users_google_id_role", table_name="users")
	op.drop_index("ix_users_email_role", table_name="users")
	op.drop_index("ix_users_email", table_name="users")

	# Re-create original single-column unique index on email
	op.create_index("ix_users_email", "users", ["email"], unique=True)

	# Re-create original unique constraint on google_id
	op.create_unique_constraint("users_google_id_key", "users", ["google_id"])
