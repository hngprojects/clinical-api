import logging
from pathlib import Path
from uuid import UUID, uuid4

from app.core.config import get_settings
from app.core.exceptions import BadGatewayError, ServerError
from app.models.medical_upload import MedicalUpload
from app.repositories.medical_upload import MedicalUploadRepository

logger = logging.getLogger(__name__)


async def upload_medical_file(
	upload_repo: MedicalUploadRepository,
	data: bytes,
	filename: str,
	content_type: str,
	medical_case_id: UUID,
	public_url_base: str,
) -> dict:
	settings = get_settings()

	media_root = Path(settings.MEDIA_DIR)
	media_root.mkdir(parents=True, exist_ok=True)

	file_type: str = content_type or "application/octet-stream"
	file_size: int = len(data)

	ext = filename.split(".")[-1] if "." in filename else ""
	file_name = f"{uuid4()}.{ext}" if ext else str(uuid4())
	local_path = media_root / file_name
	file_url = f"{public_url_base.rstrip('/')}/media/{file_name}"

	file_metadata: dict = {
		"filename": filename,
		"file_type": file_type,
		"file_size": file_size,
		"file_url": file_url,
	}

	try:
		local_path.write_bytes(data)
	except OSError as e:
		logger.error("Failed to write %s to local media directory: %s", filename, e)
		raise BadGatewayError("File failed to upload. Please try again after some time.") from e

	try:
		medical_upload: MedicalUpload = MedicalUpload(medical_case_id=medical_case_id, file=file_metadata)
		upload_repo.add(medical_upload)

		await upload_repo.flush()
		await upload_repo.refresh(medical_upload)
		await upload_repo.commit()

		return file_metadata
	except Exception as e:
		await upload_repo.rollback()
		raise ServerError("An unexpected error occurred. Please try again later.") from e
