import uuid
from datetime import datetime, timezone
from typing import TYPE_CHECKING

from sqlalchemy import DateTime, ForeignKey, Integer, String, Text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base

if TYPE_CHECKING:
	from app.models.lab_result import LabResult


class PipelineAuditLog(Base):
	"""Records every status transition in the lab result processing pipeline."""

	__tablename__ = "pipeline_audit_logs"

	id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
	lab_result_id: Mapped[uuid.UUID] = mapped_column(
		UUID(as_uuid=True),
		ForeignKey("lab_results.id", ondelete="CASCADE"),
		nullable=False,
		index=True,
	)
	event: Mapped[str] = mapped_column(String, nullable=False)
	status_before: Mapped[str | None] = mapped_column(String, nullable=True)
	status_after: Mapped[str | None] = mapped_column(String, nullable=True)
	provider: Mapped[str | None] = mapped_column(String, nullable=True)
	duration_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
	attempt: Mapped[int | None] = mapped_column(Integer, nullable=True)
	error: Mapped[str | None] = mapped_column(Text, nullable=True)
	created_at: Mapped[datetime] = mapped_column(
		DateTime(timezone=True), nullable=False, default=lambda: datetime.now(timezone.utc)
	)

	lab_result: Mapped["LabResult"] = relationship(back_populates="audit_logs")
