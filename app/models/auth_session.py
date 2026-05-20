import uuid
from datetime import datetime, timezone
from typing import TYPE_CHECKING

from sqlalchemy import Boolean, DateTime, ForeignKey, Index, String, Text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base

if TYPE_CHECKING:
	from app.models.user import User


class AuthSession(Base):
	"""Per-device authenticated session; owns the refresh token for that device."""

	__tablename__ = "auth_sessions"
	__table_args__ = (Index("ix_auth_sessions_user_id", "user_id"),)

	id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
	user_id: Mapped[uuid.UUID] = mapped_column(
		UUID(as_uuid=True),
		ForeignKey("users.id", ondelete="CASCADE"),
		nullable=False,
	)
	device_id: Mapped[str] = mapped_column(String(255), nullable=False)
	refresh_token: Mapped[str] = mapped_column(String(512), nullable=False, unique=True, index=True)
	ip_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
	user_agent: Mapped[str | None] = mapped_column(Text, nullable=True)
	created_at: Mapped[datetime] = mapped_column(
		DateTime(timezone=True),
		nullable=False,
		default=lambda: datetime.now(timezone.utc),
	)
	last_used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
	revoked: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
	revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

	user: Mapped["User"] = relationship(foreign_keys=[user_id])
