"""
File validation service.

Flow: receive UploadFile → validate size → validate type via magic bytes →
return ValidatedFile(content, filename, media_type).

Storage is handled separately — see issue #4.

Raises HTTPException(422) for invalid type/size.
"""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass

from fastapi import HTTPException, UploadFile, status

logger = logging.getLogger(__name__)

MAX_FILE_SIZE = 10 * 1024 * 1024  # 10 MB

_ALLOWED_SIGNATURES: list[tuple[bytes, str, str]] = [
	(b"%PDF", "application/pdf", ".pdf"),
	(b"\xff\xd8\xff", "image/jpeg", ".jpg"),
	(b"\x89PNG\r\n\x1a\n", "image/png", ".png"),
]


@dataclass
class ValidatedFile:
	content: bytes
	filename: str
	media_type: str


def _detect_type(content: bytes) -> tuple[str, str] | None:
	"""Return (media_type, extension) if the magic bytes match an allowed type."""
	for magic, media_type, ext in _ALLOWED_SIGNATURES:
		if content[: len(magic)] == magic:
			return media_type, ext
	return None


async def validate_file(file: UploadFile) -> ValidatedFile:
	"""Validate *file* size and type.

	Returns a ValidatedFile ready to be passed to the storage service (issue #4).
	"""
	content = await file.read()

	if len(content) > MAX_FILE_SIZE:
		raise HTTPException(
			status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
			detail="File too large. Maximum allowed size is 10 MB.",
		)

	detected = _detect_type(content)
	if detected is None:
		raise HTTPException(
			status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
			detail="Unsupported file type. Only PDF, JPG, and PNG are accepted.",
		)

	media_type, ext = detected
	unique_name = f"{uuid.uuid4()}{ext}"
	original_name = file.filename or unique_name

	logger.info("[upload] validated %s (%s)", original_name, media_type)
	return ValidatedFile(content=content, filename=original_name, media_type=media_type)
