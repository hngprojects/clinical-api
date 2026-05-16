from fastapi import APIRouter, HTTPException, UploadFile, status

from app.services.upload import validate_file

router = APIRouter(tags=["upload"])


@router.post(
	"/upload",
	status_code=status.HTTP_201_CREATED,
)
async def upload(file: UploadFile) -> None:
	"""Upload a lab result file.

	Accepts multipart/form-data with a single `file` field (PDF, JPG, or PNG,
	max 10 MB).

	Storage integration pending — issue #4. Once the storage service is merged,
	this endpoint will persist the file and return a LabResult record.
	"""
	await validate_file(file)

	raise HTTPException(
		status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
		detail="Storage service not yet available. Integration in progress.",
	)
