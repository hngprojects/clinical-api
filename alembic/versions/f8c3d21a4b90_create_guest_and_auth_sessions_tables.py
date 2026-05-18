"""create guest_sessions and auth_sessions tables; FK medical_cases.guest_session_id

Revision ID: f8c3d21a4b90
Revises: e425a99e7480
Create Date: 2026-05-16 12:00:00.000000
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy import inspect
from sqlalchemy.dialects import postgresql

revision: str = "f8c3d21a4b90"
down_revision: Union[str, Sequence[str], None] = "e425a99e7480"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

UUID_PK = postgresql.UUID(as_uuid=True)
UUID_FK = postgresql.UUID(as_uuid=True)

_GUEST_SESSION_UUID_REGEX = (
	"^[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$"
)


def upgrade() -> None:
	bind = op.get_bind()
	insp = inspect(bind)

	if not insp.has_table("guest_sessions"):
		op.create_table(
			"guest_sessions",
			sa.Column("id", UUID_PK, nullable=False),
			sa.Column("ip_hash", sa.String(length=64), nullable=False),
			sa.Column("device_fingerprint", sa.String(length=255), nullable=True),
			sa.Column("chat_count", sa.Integer(), nullable=False, server_default="0"),
			sa.Column("upload_count", sa.Integer(), nullable=False, server_default="0"),
			sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
			sa.Column("last_active_at", sa.DateTime(timezone=True), nullable=False),
			sa.Column("revoked", sa.Boolean(), nullable=False, server_default=sa.false()),
			sa.Column("migrated_user_id", UUID_FK, nullable=True),
			sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
			sa.ForeignKeyConstraint(["migrated_user_id"], ["users.id"], ondelete="SET NULL"),
			sa.PrimaryKeyConstraint("id"),
		)
		op.create_index("ix_guest_sessions_expires_at", "guest_sessions", ["expires_at"])
		op.create_index(
			"ix_guest_sessions_ip_hash_device_fingerprint",
			"guest_sessions",
			["ip_hash", "device_fingerprint"],
		)
		op.create_index("ix_guest_sessions_migrated_user_id", "guest_sessions", ["migrated_user_id"])

	if not insp.has_table("auth_sessions"):
		op.create_table(
			"auth_sessions",
			sa.Column("id", UUID_PK, nullable=False),
			sa.Column("user_id", UUID_FK, nullable=False),
			sa.Column("device_id", sa.String(length=255), nullable=False),
			sa.Column("refresh_token", sa.String(length=512), nullable=False),
			sa.Column("ip_hash", sa.String(length=64), nullable=True),
			sa.Column("user_agent", sa.Text(), nullable=True),
			sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
			sa.Column("last_used_at", sa.DateTime(timezone=True), nullable=True),
			sa.Column("revoked", sa.Boolean(), nullable=False, server_default=sa.false()),
			sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
			sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
			sa.PrimaryKeyConstraint("id"),
			sa.UniqueConstraint("refresh_token", name="uq_auth_sessions_refresh_token"),
		)
		op.create_index("ix_auth_sessions_user_id", "auth_sessions", ["user_id"])

	if not insp.has_table("medical_cases"):
		return

	columns = {column["name"]: column for column in insp.get_columns("medical_cases")}
	if "guest_session_id" not in columns:
		return

	column_type = columns["guest_session_id"]["type"]
	is_string = isinstance(column_type, sa.String) or getattr(column_type, "length", None) is not None

	if is_string:
		op.execute(
			sa.text(
				f"""
				UPDATE medical_cases
				SET guest_session_id = NULL
				WHERE guest_session_id IS NOT NULL
				  AND guest_session_id !~* '{_GUEST_SESSION_UUID_REGEX}'
				"""
			)
		)
		op.execute(
			sa.text(
				"""
				INSERT INTO guest_sessions (
					id, ip_hash, device_fingerprint, chat_count, upload_count,
					expires_at, last_active_at, revoked, created_at
				)
				SELECT DISTINCT
					guest_session_id::uuid,
					'legacy',
					NULL,
					0,
					0,
					NOW() AT TIME ZONE 'utc' + interval '24 hours',
					NOW() AT TIME ZONE 'utc',
					false,
					NOW() AT TIME ZONE 'utc'
				FROM medical_cases
				WHERE guest_session_id IS NOT NULL
				ON CONFLICT (id) DO NOTHING
				"""
			)
		)
		op.alter_column(
			"medical_cases",
			"guest_session_id",
			existing_type=sa.String(),
			type_=UUID_FK,
			postgresql_using="guest_session_id::uuid",
			existing_nullable=True,
		)

	fks = {fk["name"] for fk in insp.get_foreign_keys("medical_cases")}
	if "fk_medical_cases_guest_session_id_guest_sessions" not in fks:
		op.create_foreign_key(
			"fk_medical_cases_guest_session_id_guest_sessions",
			"medical_cases",
			"guest_sessions",
			["guest_session_id"],
			["id"],
			ondelete="SET NULL",
		)

	indexes = {idx["name"] for idx in insp.get_indexes("medical_cases")}
	if "ix_medical_cases_guest_session_id" not in indexes:
		op.create_index("ix_medical_cases_guest_session_id", "medical_cases", ["guest_session_id"])


def downgrade() -> None:
	bind = op.get_bind()
	insp = inspect(bind)

	if insp.has_table("medical_cases"):
		fks = {fk["name"] for fk in insp.get_foreign_keys("medical_cases")}
		if "fk_medical_cases_guest_session_id_guest_sessions" in fks:
			op.drop_constraint(
				"fk_medical_cases_guest_session_id_guest_sessions",
				"medical_cases",
				type_="foreignkey",
			)
		indexes = {idx["name"] for idx in insp.get_indexes("medical_cases")}
		if "ix_medical_cases_guest_session_id" in indexes:
			op.drop_index("ix_medical_cases_guest_session_id", table_name="medical_cases")

		columns = {column["name"]: column for column in insp.get_columns("medical_cases")}
		if "guest_session_id" in columns:
			column_type = columns["guest_session_id"]["type"]
			if isinstance(column_type, postgresql.UUID):
				op.alter_column(
					"medical_cases",
					"guest_session_id",
					existing_type=UUID_FK,
					type_=sa.String(),
					postgresql_using="guest_session_id::text",
					existing_nullable=True,
				)

	if insp.has_table("auth_sessions"):
		op.drop_table("auth_sessions")

	if insp.has_table("guest_sessions"):
		op.drop_table("guest_sessions")
