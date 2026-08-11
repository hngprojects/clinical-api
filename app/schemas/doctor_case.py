from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict

from app.models.medical_case import DoctorCaseStatus


class DoctorCasePreviewItem(BaseModel):
	"""Minimal row used by doctor dashboard case lists."""

	case_id: UUID
	patient_name: str | None = None
	patient_avatar_url: str | None = None
	requested_review: str | None = None
	status: DoctorCaseStatus
	received_at: datetime
	updated_at: datetime

	model_config = ConfigDict(from_attributes=True)
