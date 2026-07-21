import enum
import uuid
from datetime import datetime, timezone
from typing import TYPE_CHECKING

from sqlalchemy import DateTime, Enum, ForeignKey, String
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base

if TYPE_CHECKING:
	from app.models.user import User


class DoctorVerificationStatus(str, enum.Enum):
	"""Verification status of a doctor."""

	NOT_SUBMITTED = "not_submitted"
	PENDING = "pending"
	APPROVED = "approved"
	REJECTED = "rejected"


class DoctorVerification(Base):
	__tablename__ = "doctor_verifications"

	id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
	user_id: Mapped[uuid.UUID] = mapped_column(
		UUID(as_uuid=True),
		ForeignKey("users.id", ondelete="CASCADE"),
		nullable=False,
		unique=True,
		index=True,
	)
	license_number: Mapped[str] = mapped_column(String, nullable=False)
	issuing_state: Mapped[str] = mapped_column(String, nullable=False)
	license_expiry_date: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
	specialty: Mapped[str] = mapped_column(String, nullable=False)
	status: Mapped[DoctorVerificationStatus] = mapped_column(
		Enum(DoctorVerificationStatus, values_callable=lambda obj: [e.value for e in obj]),
		nullable=False,
		default=DoctorVerificationStatus.PENDING,
	)
	rejection_reason: Mapped[str | None] = mapped_column(String, nullable=True)
	created_at: Mapped[datetime] = mapped_column(
		DateTime(timezone=True),
		nullable=False,
		default=lambda: datetime.now(timezone.utc),
	)
	updated_at: Mapped[datetime] = mapped_column(
		DateTime(timezone=True),
		nullable=False,
		default=lambda: datetime.now(timezone.utc),
		onupdate=lambda: datetime.now(timezone.utc),
	)

	user: Mapped["User"] = relationship(back_populates="doctor_verification")
