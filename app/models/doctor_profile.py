import enum
import uuid
from datetime import datetime, timezone
from typing import TYPE_CHECKING

from sqlalchemy import DateTime, Enum, Integer, String, Text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base

if TYPE_CHECKING:
	from app.models.user import User


class DoctorVerificationStatus(str, enum.Enum):
	INCOMPLETE = "incomplete"  # hasn't finished the form yet
	PENDING_REVIEW = "pending_review"  # submitted, waiting for admin
	VERIFIED = "verified"  # approved
	REJECTED = "rejected"  # rejected by admin


class DoctorProfile(Base):
	__tablename__ = "doctor_profiles"

	id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
	user_id: Mapped[uuid.UUID] = mapped_column(
		UUID(as_uuid=True),
		__import__("sqlalchemy", fromlist=["ForeignKey"]).ForeignKey("users.id", ondelete="CASCADE"),
		unique=True,
		nullable=False,
		index=True,
	)

	# Step 1 — Professional Information
	specialization: Mapped[str | None] = mapped_column(String, nullable=True)
	years_of_experience: Mapped[int | None] = mapped_column(Integer, nullable=True)
	hospital: Mapped[str | None] = mapped_column(String, nullable=True)  # optional
	passport_photo_url: Mapped[str | None] = mapped_column(String, nullable=True)

	# Step 2 — Credential Verification
	mdcn_license_number: Mapped[str | None] = mapped_column(String, nullable=True)
	nin: Mapped[str | None] = mapped_column(String, nullable=True)
	medical_license_url: Mapped[str | None] = mapped_column(String, nullable=True)

	# Verification state
	verification_status: Mapped[DoctorVerificationStatus] = mapped_column(
		Enum(DoctorVerificationStatus, values_callable=lambda obj: [e.value for e in obj]),
		nullable=False,
		default=DoctorVerificationStatus.INCOMPLETE,
	)
	rejection_reason: Mapped[str | None] = mapped_column(Text, nullable=True)

	submitted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
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

	user: Mapped["User"] = relationship(back_populates="doctor_profile")
