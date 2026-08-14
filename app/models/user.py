import enum
import uuid
from datetime import datetime, timezone
from typing import TYPE_CHECKING

from sqlalchemy import Boolean, DateTime, Enum, Index, String, text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base

if TYPE_CHECKING:
	from app.models.chat import Chat
	from app.models.doctor_verification import DoctorVerification
	from app.models.guest_session import GuestSession
	from app.models.medical_case import MedicalCase
	from app.models.notification import Notification
	from app.models.otp import OtpCode


class UserRole(str, enum.Enum):
	"""Role of the user in the system."""

	PATIENT = "patient"
	DOCTOR = "doctor"
	ADMIN = "admin"


class User(Base):
	__tablename__ = "users"

	id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
	email: Mapped[str] = mapped_column(String, nullable=False, index=True)
	pending_email: Mapped[str | None] = mapped_column(String, nullable=True)
	email_change_token: Mapped[str | None] = mapped_column(String, nullable=True)
	password_hash: Mapped[str | None] = mapped_column(String, nullable=True)
	google_id: Mapped[str | None] = mapped_column(String, nullable=True)

	__table_args__ = (
		Index("ix_users_email_role", "email", "role", unique=True),
		Index(
			"ix_users_google_id_role",
			"google_id",
			"role",
			unique=True,
			postgresql_where=text("google_id IS NOT NULL"),
		),
	)
	first_name: Mapped[str] = mapped_column(String, nullable=False)
	last_name: Mapped[str] = mapped_column(String, nullable=False)
	role: Mapped[UserRole] = mapped_column(
		Enum(UserRole, values_callable=lambda obj: [e.value for e in obj]),
		nullable=False,
		default=UserRole.PATIENT,
	)
	is_email_verified: Mapped[bool] = mapped_column(
		Boolean,
		nullable=False,
		default=False,
	)
	notify_on_complete: Mapped[bool] = mapped_column(
		Boolean,
		nullable=False,
		default=True,
	)
	is_active: Mapped[bool] = mapped_column(
		Boolean,
		nullable=False,
		default=True,
	)
	avatar_url: Mapped[str | None] = mapped_column(String, nullable=True)
	is_on_duty: Mapped[bool] = mapped_column(
		Boolean,
		nullable=False,
		default=False,
	)
	on_duty_since: Mapped[datetime | None] = mapped_column(
		DateTime(timezone=True),
		nullable=True,
	)
	is_verification_dismissed: Mapped[bool] = mapped_column(
		Boolean,
		nullable=False,
		default=False,
	)
	created_at: Mapped[datetime] = mapped_column(
		DateTime(timezone=True),
		nullable=False,
		default=lambda: datetime.now(timezone.utc),
	)
	last_login_at: Mapped[datetime | None] = mapped_column(
		DateTime(timezone=True),
		nullable=True,
	)

	medical_cases: Mapped[list["MedicalCase"]] = relationship(
		foreign_keys="MedicalCase.user_id", back_populates="user", passive_deletes=True
	)
	doctor_cases: Mapped[list["MedicalCase"]] = relationship(
		foreign_keys="MedicalCase.doctor_id", back_populates="doctor", passive_deletes=True
	)
	chats: Mapped[list["Chat"]] = relationship(back_populates="user", passive_deletes=True)
	notifications: Mapped[list["Notification"]] = relationship(back_populates="user", passive_deletes=True)
	otp_codes: Mapped[list["OtpCode"]] = relationship(back_populates="user", cascade="all, delete-orphan")
	migrated_guest_sessions: Mapped[list["GuestSession"]] = relationship(
		foreign_keys="GuestSession.migrated_user_id",
		back_populates="migrated_user",
	)
	doctor_verification: Mapped["DoctorVerification | None"] = relationship(
		back_populates="user", cascade="all, delete-orphan"
	)

	@property
	def full_name(self) -> str:
		return f"{self.first_name} {self.last_name}".strip()
