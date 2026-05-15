from fastapi import APIRouter, UploadFile, status

from app.api.deps import GuestSessionId, LabResultRepo, MedicalCaseRepo, OptionalUser
from app.core.responses import SuccessResponse
from app.schemas.lab_result import LabResultResponse, UploadRequest, UploadResponse
from app.services.lab_result import upload_lab_result
from app.services.upload import validate_and_upload

router = APIRouter(tags=["upload"])


@router.post(
	"/upload",
	response_model=SuccessResponse[UploadResponse],
	status_code=status.HTTP_201_CREATED,
)
async def upload(
	file: UploadFile,
	current_user: OptionalUser,
	guest_session_id: GuestSessionId,
	lab_repo: LabResultRepo,
	case_repo: MedicalCaseRepo,
) -> SuccessResponse[UploadResponse]:
	"""Upload a lab result file.

	Accepts multipart/form-data with a single `file` field (PDF, JPG, or PNG,
	max 10 MB).  Creates a MedicalCase and a LabResult atomically, then fires
	the OCR → AI pipeline.

	Authentication is optional:
	- Authenticated users send a Bearer token.
	- Guests send their session ID in the X-Guest-Session-Id header.
	"""
	file_obj = await validate_and_upload(file)

	guest_sid = None if current_user else guest_session_id
	payload = UploadRequest(file=file_obj, guest_session_id=guest_sid)

	case, lab_result = await upload_lab_result(lab_repo, case_repo, payload, current_user)

	return SuccessResponse(
		message="Upload received. Processing started.",
		data=UploadResponse(
			case_id=case.id,
			lab_result=LabResultResponse.model_validate(lab_result),
		),
	)
