import uuid
from datetime import datetime, timezone
from typing import TYPE_CHECKING

from sqlalchemy import Boolean, DateTime, ForeignKey, Index, Integer, String
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base

if TYPE_CHECKING:
	from app.models.medical_case import MedicalCase
	from app.models.user import User


class GuestSession(Base):
	"""Server-owned anonymous session with usage counters and device binding."""

	__tablename__ = "guest_sessions"
	__table_args__ = (
		Index("ix_guest_sessions_expires_at", "expires_at"),
		Index("ix_guest_sessions_ip_hash_device_fingerprint", "ip_hash", "device_fingerprint"),
	)

	id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
	ip_hash: Mapped[str] = mapped_column(String(64), nullable=False)
	device_fingerprint: Mapped[str | None] = mapped_column(String(255), nullable=True)
	chat_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
	upload_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
	expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
	last_active_at: Mapped[datetime] = mapped_column(
		DateTime(timezone=True),
		nullable=False,
		default=lambda: datetime.now(timezone.utc),
	)
	revoked: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
	migrated_user_id: Mapped[uuid.UUID | None] = mapped_column(
		UUID(as_uuid=True),
		ForeignKey("users.id", ondelete="SET NULL"),
		nullable=True,
		index=True,
	)
	created_at: Mapped[datetime] = mapped_column(
		DateTime(timezone=True),
		nullable=False,
		default=lambda: datetime.now(timezone.utc),
	)

	migrated_user: Mapped["User | None"] = relationship(foreign_keys=[migrated_user_id])
	medical_cases: Mapped[list["MedicalCase"]] = relationship(back_populates="guest_session")
