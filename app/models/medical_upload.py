import uuid
from datetime import datetime, timezone
from typing import TYPE_CHECKING

from sqlalchemy import DateTime, ForeignKey
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base

if TYPE_CHECKING:
	from app.models.medical_case import MedicalCase


class MedicalUpload(Base):
	"""Files uploaded during a medical case"""

	__tablename__ = "medical_uploads"

	id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
	medical_case_id: Mapped[uuid.UUID] = mapped_column(
		UUID(as_uuid=True), ForeignKey("medical_cases.id", ondelete="CASCADE"), index=True, nullable=False
	)
	file: Mapped[dict] = mapped_column(JSONB, nullable=False)  # represent file metadata
	created_at: Mapped[datetime] = mapped_column(
		DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), nullable=False
	)

	medical_case: Mapped["MedicalCase"] = relationship(back_populates="medical_upload", viewonly=True)
