import enum
import uuid
from datetime import datetime, timezone
from typing import TYPE_CHECKING

from sqlalchemy import DateTime, Enum, ForeignKey, Index, String
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base

if TYPE_CHECKING:
	from app.models.ai_interpretation import AIInterpretation
	from app.models.chat import Chat
	from app.models.guest_session import GuestSession
	from app.models.lab_result import LabResult
	from app.models.notification import Notification
	from app.models.user import User


class MedicalCaseStatus(str, enum.Enum):
	"""Status of a medical case."""

	PENDING = "pending"
	PROCESSING = "processing"
	COMPLETE = "complete"
	FAILED = "failed"


class DoctorCaseStatus(str, enum.Enum):
	"""Lifecycle of a case request assigned to a doctor."""

	PENDING = "pending"
	ACCEPTED = "accepted"
	DECLINED = "declined"
	CANCELLED = "cancelled"


class MedicalCase(Base):
	__tablename__ = "medical_cases"
	__table_args__ = (
		Index("ix_medical_cases_doctor_case_status_created", "doctor_id", "doctor_case_status", "created_at"),
		Index("ix_medical_cases_doctor_case_status_updated", "doctor_id", "doctor_case_status", "updated_at"),
	)

	id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
	user_id: Mapped[uuid.UUID | None] = mapped_column(
		UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=True, index=True
	)
	guest_session_id: Mapped[uuid.UUID | None] = mapped_column(
		UUID(as_uuid=True),
		ForeignKey("guest_sessions.id", ondelete="SET NULL"),
		nullable=True,
		index=True,
	)
	status: Mapped[MedicalCaseStatus] = mapped_column(
		Enum(MedicalCaseStatus, values_callable=lambda obj: [e.value for e in obj]), nullable=False
	)
	doctor_id: Mapped[uuid.UUID | None] = mapped_column(
		UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
	)
	doctor_case_status: Mapped[DoctorCaseStatus | None] = mapped_column(
		Enum(DoctorCaseStatus, values_callable=lambda obj: [e.value for e in obj], name="doctorcasestatus"),
		nullable=True,
	)
	title: Mapped[str | None] = mapped_column(String, nullable=True)
	created_at: Mapped[datetime] = mapped_column(
		DateTime(timezone=True), nullable=False, default=lambda: datetime.now(timezone.utc)
	)
	updated_at: Mapped[datetime] = mapped_column(
		DateTime(timezone=True),
		nullable=False,
		default=lambda: datetime.now(timezone.utc),
		onupdate=lambda: datetime.now(timezone.utc),
	)
	completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

	user: Mapped["User"] = relationship(foreign_keys=[user_id], back_populates="medical_cases")
	doctor: Mapped["User | None"] = relationship(foreign_keys=[doctor_id], back_populates="doctor_cases")
	guest_session: Mapped["GuestSession | None"] = relationship(back_populates="medical_cases")
	lab_results: Mapped[list["LabResult"]] = relationship(back_populates="medical_case", passive_deletes=True)
	ai_interpretations: Mapped[list["AIInterpretation"]] = relationship(
		back_populates="medical_case", passive_deletes=True
	)
	chats: Mapped[list["Chat"]] = relationship(back_populates="medical_case", passive_deletes=True)
	notifications: Mapped[list["Notification"]] = relationship(back_populates="medical_case", passive_deletes=True)
