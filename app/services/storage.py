from pathlib import Path
from uuid import UUID, uuid4

import aiofiles
from fastapi import UploadFile

from app.core.config import get_settings
from app.core.exceptions import ServerError
from app.models.medical_upload import MedicalUpload
from app.repositories.medical_upload import MedicalUploadRepository


async def upload_medical_file(upload_repo: MedicalUploadRepository, file: UploadFile, medical_case_id: UUID):
	await file.seek(0)  # reset file pointer
	settings = get_settings()

	file_size: int = file.size
	filename: str = file.filename
	file_type: str = file.content_type

	ext: str = filename.split(".")[-1]
	filename: str = f"{uuid4()}.{ext}"

	file_metadata: dict = {"file_size": file_size, "filename": filename, "file_type": file_type}

	file_path: Path = Path(__file__).parent.parent / "uploads" / filename

	try:
		async with aiofiles.open(file_path, "wb+") as f:
			while chunk := await file.read(settings.CHUNK_SIZE):
				await f.write(chunk)

		medical_upload: MedicalUpload = MedicalUpload(medical_case_id=medical_case_id, file=file_metadata)
		upload_repo.add(medical_upload)

		await upload_repo.flush()
		await upload_repo.refresh(medical_upload)
		await upload_repo.commit()
	except Exception as e:
		await upload_repo.rollback()
		raise ServerError("An unexpected error occurred. Please try again later.") from e
