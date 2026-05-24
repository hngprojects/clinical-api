import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field, field_serializer

from app.models.doctor_profile import DoctorVerificationStatus


class ProfessionalInfoRequest(BaseModel):
	"""Step 1 — Professional Information."""

	model_config = ConfigDict(str_strip_whitespace=True)

	specialization: str = Field(min_length=1, max_length=100)
	years_of_experience: int = Field(ge=0, le=60)
	hospital: str | None = Field(default=None, max_length=200)


class CredentialsRequest(BaseModel):
	"""Step 2 — Credential Verification."""

	model_config = ConfigDict(str_strip_whitespace=True)

	mdcn_license_number: str = Field(min_length=1, max_length=50)
	nin: str = Field(min_length=11, max_length=11)


class DoctorProfileResponse(BaseModel):
	"""Full doctor profile response."""

	id: uuid.UUID
	user_id: uuid.UUID
	specialization: str | None = None
	years_of_experience: int | None = None
	hospital: str | None = None
	passport_photo_url: str | None = None
	mdcn_license_number: str | None = None
	nin: str | None = None
	medical_license_url: str | None = None
	verification_status: DoctorVerificationStatus
	rejection_reason: str | None = None
	submitted_at: datetime | None = None
	created_at: datetime
	updated_at: datetime

	model_config = ConfigDict(from_attributes=True)

	@field_serializer("mdcn_license_number", "nin")
	def _mask_sensitive_identifier(self, value: str | None) -> str | None:
		if value is None:
			return None
		if len(value) <= 4:
			return "*" * len(value)
		return f"{'*' * (len(value) - 4)}{value[-4:]}"
