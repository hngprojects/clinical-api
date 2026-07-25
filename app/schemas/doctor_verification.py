from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict


class DoctorVerificationDocumentResponse(BaseModel):
	id: UUID
	document_type: str
	filename: str
	file_size: int
	mime_type: str
	created_at: datetime

	model_config = ConfigDict(from_attributes=True)


class DoctorVerificationResponse(BaseModel):
	id: UUID
	user_id: UUID
	license_number: str
	issuing_state: str
	license_expiry_date: datetime
	specialty: str
	status: str
	rejection_reason: str | None = None
	created_at: datetime
	updated_at: datetime
	documents: list[DoctorVerificationDocumentResponse]

	model_config = ConfigDict(from_attributes=True)


class SignedUrlResponse(BaseModel):
	signed_url: str
