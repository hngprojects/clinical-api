"""
File validation and storage service.

Flow: receive UploadFile → validate size → validate type via magic bytes →
upload to configured storage provider → return FileObject(name, url).

Raises HTTPException(422) for invalid type/size.
Raises HTTPException(502) if the storage backend fails (no DB writes have
happened yet at that point, satisfying the "storage failure ≠ DB record" rule).
"""

from __future__ import annotations

import logging
import uuid
from pathlib import Path

from fastapi import HTTPException, UploadFile, status

from app.core.config import get_settings
from app.schemas.lab_result import FileObject

logger = logging.getLogger(__name__)

MAX_FILE_SIZE = 10 * 1024 * 1024  # 10 MB

_ALLOWED_SIGNATURES: list[tuple[bytes, str, str]] = [
	(b"%PDF", "application/pdf", ".pdf"),
	(b"\xff\xd8\xff", "image/jpeg", ".jpg"),
	(b"\x89PNG\r\n\x1a\n", "image/png", ".png"),
]


def _detect_type(content: bytes) -> tuple[str, str] | None:
	"""Return (media_type, extension) if the magic bytes match an allowed type."""
	for magic, media_type, ext in _ALLOWED_SIGNATURES:
		if content[: len(magic)] == magic:
			return media_type, ext
	return None


# Storage backends


async def _upload_local(content: bytes, filename: str) -> str:
	"""Save to /tmp and return a file:// URL. Dev-only fallback."""
	try:
		dest = Path("/tmp/clinsights_uploads")
		dest.mkdir(parents=True, exist_ok=True)
		out = dest / filename
		out.write_bytes(content)
		return f"file://{out}"
	except OSError as exc:
		logger.error("[upload] local storage failed for %s: %s", filename, exc)
		raise HTTPException(
			status_code=status.HTTP_502_BAD_GATEWAY,
			detail="File could not be stored. Please try again.",
		) from exc


async def _upload_s3(content: bytes, filename: str, media_type: str) -> str:
	"""Upload to S3 and return the public HTTPS URL."""
	import boto3
	from botocore.exceptions import BotoCoreError, ClientError

	settings = get_settings()
	try:
		client = boto3.client(
			"s3",
			region_name=settings.AWS_S3_REGION,
			aws_access_key_id=settings.AWS_ACCESS_KEY_ID,
			aws_secret_access_key=settings.AWS_SECRET_ACCESS_KEY,
		)
		client.put_object(
			Bucket=settings.AWS_S3_BUCKET,
			Key=f"uploads/{filename}",
			Body=content,
			ContentType=media_type,
		)
	except (BotoCoreError, ClientError) as exc:
		logger.error("[upload] S3 upload failed for %s: %s", filename, exc)
		raise HTTPException(
			status_code=status.HTTP_502_BAD_GATEWAY,
			detail="File could not be stored. Please try again.",
		) from exc

	base = settings.STORAGE_BASE_URL.rstrip("/")
	return f"{base}/uploads/{filename}"


# Public API


async def validate_and_upload(file: UploadFile) -> FileObject:
	"""Validate *file* and persist it to the configured storage provider.

	Returns a FileObject ready to be stored on the LabResult record.
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

	settings = get_settings()
	try:
		if settings.STORAGE_PROVIDER == "s3":
			url = await _upload_s3(content, unique_name, media_type)
		else:
			url = await _upload_local(content, unique_name)
			logger.warning("[upload] Using local storage — not suitable for production.")
	except HTTPException:
		raise
	except Exception as exc:
		logger.error("[upload] unexpected storage error for %s: %s", unique_name, exc)
		raise HTTPException(
			status_code=status.HTTP_502_BAD_GATEWAY,
			detail="File could not be stored. Please try again.",
		) from exc

	original_name = file.filename or unique_name
	logger.info("[upload] stored %s as %s via %s", original_name, unique_name, settings.STORAGE_PROVIDER)

	return FileObject(name=original_name, url=url)
