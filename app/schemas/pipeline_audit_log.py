from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict


class PipelineAuditLogResponse(BaseModel):
	"""Response schema for a pipeline audit log entry."""

	id: UUID
	lab_result_id: UUID
	event: str
	status_before: str | None = None
	status_after: str | None = None
	provider: str | None = None
	duration_ms: int | None = None
	attempt: int | None = None
	error: str | None = None
	created_at: datetime

	model_config = ConfigDict(from_attributes=True)
