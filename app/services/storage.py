import io
import logging
from uuid import UUID, uuid4

from fastapi import UploadFile
from minio import Minio
from minio.error import S3Error

from app.core.config import get_settings
from app.core.exceptions import BadGatewayError, ServerError
from app.models.medical_upload import MedicalUpload
from app.repositories.medical_upload import MedicalUploadRepository

logger = logging.getLogger(__name__)


async def upload_medical_file(upload_repo: MedicalUploadRepository, file: UploadFile, medical_case_id: UUID) -> dict:
	settings = get_settings()

	minio_client = Minio(
		settings.MINIO_URL,
		access_key=settings.MINIO_USERNAME,
		secret_key=settings.MINIO_PASSWORD,
		secure=settings.MINIO_SECURE,
	)

	await file.seek(0)  # reset file pointer

	file_size: int = file.size
	filename: str = file.filename
	file_type: str = file.content_type

	ext: str = filename.split(".")[-1]
	file_url: str = f"{uuid4()}.{ext}"  # a unique string for each file uploaded

	file_metadata: dict = {"filename": filename, "file_type": file_type, "file_size": file_size, "file_url": file_url}

	try:
		if not minio_client.bucket_exists(settings.MINIO_BUCKET_NAME):
			minio_client.make_bucket(settings.MINIO_BUCKET_NAME)

		data: bytes = await file.read()
		minio_client.put_object(
			bucket_name=settings.MINIO_BUCKET_NAME,
			object_name=file_url,  # object name to retrieve the file from minio
			data=io.BytesIO(data),
			length=len(data),
			content_type=file_type,
		)
	except S3Error as e:
		logger.error("Failed to upload %s to MinIO: %s", filename, e.message)
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
