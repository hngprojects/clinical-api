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
	documents: Mapped[list["DoctorVerificationDocument"]] = relationship(
		back_populates="verification", cascade="all, delete-orphan"
	)
	audit_logs: Mapped[list["DoctorVerificationAuditLog"]] = relationship(
		back_populates="verification", cascade="all, delete-orphan"
	)


class DoctorVerificationDocument(Base):
	__tablename__ = "doctor_verification_documents"

	id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
	verification_id: Mapped[uuid.UUID] = mapped_column(
		UUID(as_uuid=True),
		ForeignKey("doctor_verifications.id", ondelete="CASCADE"),
		nullable=False,
		index=True,
	)
	document_type: Mapped[str] = mapped_column(
		String, nullable=False
	)  # 'medical_license', 'government_id', 'board_certification'
	file_path: Mapped[str] = mapped_column(String, nullable=False)  # R2 key or local private file path
	storage_type: Mapped[str] = mapped_column(String, nullable=False, default="local")
	filename: Mapped[str] = mapped_column(String, nullable=False)
	file_size: Mapped[int] = mapped_column(nullable=False)
	mime_type: Mapped[str] = mapped_column(String, nullable=False)
	created_at: Mapped[datetime] = mapped_column(
		DateTime(timezone=True),
		nullable=False,
		default=lambda: datetime.now(timezone.utc),
	)

	verification: Mapped["DoctorVerification"] = relationship(back_populates="documents")


class DoctorVerificationAuditLog(Base):
	__tablename__ = "doctor_verification_audit_logs"

	id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
	verification_id: Mapped[uuid.UUID] = mapped_column(
		UUID(as_uuid=True),
		ForeignKey("doctor_verifications.id", ondelete="CASCADE"),
		nullable=False,
		index=True,
	)
	status_before: Mapped[str] = mapped_column(String, nullable=False)
	status_after: Mapped[str] = mapped_column(String, nullable=False)
	changed_by: Mapped[uuid.UUID | None] = mapped_column(
		UUID(as_uuid=True),
		ForeignKey("users.id", ondelete="SET NULL"),
		nullable=True,
	)
	rejection_reason: Mapped[str | None] = mapped_column(String, nullable=True)
	created_at: Mapped[datetime] = mapped_column(
		DateTime(timezone=True),
		nullable=False,
		default=lambda: datetime.now(timezone.utc),
	)

	verification: Mapped["DoctorVerification"] = relationship(back_populates="audit_logs")
