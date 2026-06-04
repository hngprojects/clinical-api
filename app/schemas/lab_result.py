from __future__ import annotations

import mimetypes
from datetime import datetime
from typing import Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict, model_validator

from app.models.lab_result import OCRStatus


def _infer_mime_type(payload: dict[str, Any]) -> str:
	mime_type = payload.get("mime_type")
	if isinstance(mime_type, str) and mime_type.strip():
		return mime_type.strip()

	legacy_mime_type = payload.get("file_type")
	if isinstance(legacy_mime_type, str) and legacy_mime_type.strip():
		return legacy_mime_type.strip()

	for key in ("name", "url", "filename", "file_url"):
		value = payload.get(key)
		if isinstance(value, str):
			guessed, _ = mimetypes.guess_type(value)
			if guessed:
				return guessed

	return "application/octet-stream"


class FileObject(BaseModel):
	"""Represents an uploaded file."""

	name: str
	url: str
	mime_type: str

	@model_validator(mode="before")
	@classmethod
	def normalize_legacy_payload(cls, value: Any) -> Any:
		if not isinstance(value, dict):
			return value

		payload = dict(value)

		if "name" not in payload and isinstance(payload.get("filename"), str):
			payload["name"] = payload["filename"]

		if "url" not in payload and isinstance(payload.get("file_url"), str):
			payload["url"] = payload["file_url"]

		payload["mime_type"] = _infer_mime_type(payload)
		return payload


class LabResultBase(BaseModel):
	file: FileObject
	ocr_status: OCRStatus


class LabResultCreate(LabResultBase):
	"""Schema for creating a lab result."""

	medical_case_id: UUID


class UploadRequest(BaseModel):
	"""Single-action upload schema.

	The frontend sends this when a user selects a file.  The backend creates
	the MedicalCase and the LabResult atomically, then fires the pipeline.
	No prior case creation step is needed.
	"""

	file: FileObject
	guest_session_id: str | None = None


class LabResultUpdate(BaseModel):
	"""Schema for updating a lab result after OCR processing."""

	ocr_status: OCRStatus | None = None
	extracted_values: dict[str, Any] | None = None
	ocr_completed_at: datetime | None = None


class LabResultResponse(LabResultBase):
	"""Response schema for a lab result."""

	id: UUID
	medical_case_id: UUID
	extracted_values: dict[str, Any] | None = None
	ocr_completed_at: datetime | None = None
	created_at: datetime

	model_config = ConfigDict(from_attributes=True)


class UploadResponse(BaseModel):
	"""Response after a successful upload."""

	case_id: UUID
	lab_result: LabResultResponse

	model_config = ConfigDict(from_attributes=True)
