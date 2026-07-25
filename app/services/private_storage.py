import logging
from datetime import datetime, timedelta, timezone
from functools import partial
from io import BytesIO
from pathlib import Path
from uuid import uuid4

import anyio
import jwt
from minio import Minio

from app.core.config import get_settings
from app.core.exceptions import BadGatewayError

logger = logging.getLogger(__name__)


def _get_r2_client() -> Minio | None:
	settings = get_settings()
	if not all(
		[settings.R2_ACCOUNT_ID, settings.R2_ACCESS_KEY_ID, settings.R2_SECRET_ACCESS_KEY, settings.R2_BUCKET_NAME]
	):
		return None

	endpoint = f"{settings.R2_ACCOUNT_ID}.r2.cloudflarestorage.com"
	try:
		return Minio(
			endpoint=endpoint,
			access_key=settings.R2_ACCESS_KEY_ID,
			secret_key=settings.R2_SECRET_ACCESS_KEY,
			secure=True,
		)
	except Exception:
		logger.exception("Failed to initialize R2 client")
		return None


async def upload_private_file(
	data: bytes,
	filename: str,
	content_type: str,
	subdir: str = "verifications",
) -> dict:
	"""Upload a file securely to Cloudflare R2 or secure local private storage."""
	settings = get_settings()
	ext = filename.split(".")[-1] if "." in filename else ""
	file_name = f"{uuid4()}.{ext}" if ext else str(uuid4())
	key = f"{subdir}/{file_name}" if subdir else file_name

	client = _get_r2_client()
	if client is not None:
		data_stream = BytesIO(data)
		try:
			await anyio.to_thread.run_sync(
				partial(
					client.put_object,
					settings.R2_BUCKET_NAME,
					key,
					data_stream,
					len(data),
					content_type=content_type,
				)
			)
			return {
				"filename": filename,
				"mime_type": content_type,
				"file_size": len(data),
				"file_path": key,
				"storage_type": "r2",
			}
		except Exception as exc:
			logger.exception("R2 upload failed: %s", exc)
			raise BadGatewayError("Failed to upload document to R2 storage.") from exc
	else:
		dest = Path(settings.PRIVATE_MEDIA_DIR) / subdir
		dest.mkdir(mode=0o700, parents=True, exist_ok=True)
		local_path = dest / file_name
		try:
			await anyio.to_thread.run_sync(partial(local_path.write_bytes, data))
			return {
				"filename": filename,
				"mime_type": content_type,
				"file_size": len(data),
				"file_path": str(local_path.as_posix()),
				"storage_type": "local",
			}
		except OSError as exc:
			logger.exception("Local private upload failed: %s", exc)
			raise BadGatewayError("Failed to upload document to private storage.") from exc


async def generate_document_signed_url(
	file_path_or_key: str,
	document_id: str,
	storage_type: str = "local",
	expires_in_seconds: int = 600,
) -> str:
	"""Generate a short-lived signed URL to read a private document.

	For R2 documents a presigned S3 URL is returned directly. The URL is never
	rewritten -- replacing the S3 host invalidates the HMAC signature. If R2
	credentials are missing or presigning fails, a BadGatewayError is raised;
	silently falling back to a local JWT URL for an R2 key would be unusable.

	For locally stored documents a short-lived HS256 JWT pointing at the
	/documents/{id}/view endpoint is returned.
	"""
	settings = get_settings()

	if storage_type == "r2":
		client = _get_r2_client()
		if client is None:
			raise BadGatewayError(
				"R2 credentials are not configured; cannot generate a presigned URL for a cloud-stored document."
			)
		try:
			url: str = await anyio.to_thread.run_sync(
				partial(
					client.presigned_get_object,
					settings.R2_BUCKET_NAME,
					file_path_or_key,
					timedelta(seconds=expires_in_seconds),
				)
			)
			return url
		except Exception as exc:
			logger.exception("Failed to generate presigned R2 URL for document %s", document_id)
			raise BadGatewayError("Failed to generate a presigned URL for the document.") from exc

	# Local storage fallback -- JWT signed URL
	payload = {
		"doc_id": str(document_id),
		"exp": datetime.now(timezone.utc) + timedelta(seconds=expires_in_seconds),
	}
	token = jwt.encode(payload, settings.JWT_SECRET, algorithm="HS256")
	base_url = settings.FRONTEND_URL or "http://localhost:8000"
	return f"{base_url.rstrip('/')}/api/v1/doctors/verification/documents/{document_id}/view?token={token}"


async def delete_private_file(file_path_or_key: str, storage_type: str = "local") -> None:
	"""Clean up a file from private storage.

	Blocking remove_object / unlink calls are offloaded to a thread-pool worker
	so the event loop is not stalled.
	"""
	settings = get_settings()
	if storage_type == "r2":
		client = _get_r2_client()
		if client is not None:
			try:
				await anyio.to_thread.run_sync(partial(client.remove_object, settings.R2_BUCKET_NAME, file_path_or_key))
			except Exception as exc:
				logger.warning("Failed to delete R2 object %s: %s", file_path_or_key, exc)
	else:
		path = Path(file_path_or_key)
		try:
			await anyio.to_thread.run_sync(partial(path.unlink, missing_ok=True))
		except OSError as exc:
			logger.warning("Failed to delete local private file %s: %s", path, exc)
