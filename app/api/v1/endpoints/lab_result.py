from uuid import UUID

from fastapi import APIRouter, File, Query, Request, UploadFile, status

from app.api.deps import (
	CurrentUser,
	GuestSessionManagerDep,
	LabResultRepo,
	MedicalCaseRepo,
	SessionContextDep,
)
from app.core.exceptions import BadRequestError, ForbiddenError, UnauthorizedError
from app.core.responses import SuccessResponse
from app.schemas.lab_result import LabResultResponse, UploadResponse
from app.services.guest_sessions import GuestUsageAction
from app.services.lab_result import (
	add_file_to_case,
	get_lab_result,
	handle_file_upload,
	list_lab_results_for_case,
)
from app.services.medical_case import get_case

router = APIRouter(tags=["lab-results"])


@router.post(
	"/upload",
	response_model=SuccessResponse[UploadResponse],
	status_code=status.HTTP_201_CREATED,
)
async def upload(
	request: Request,
	ctx: SessionContextDep,
	manager: GuestSessionManagerDep,
	lab_repo: LabResultRepo,
	case_repo: MedicalCaseRepo,
	file: UploadFile = File(...),
) -> SuccessResponse[UploadResponse]:
	"""Upload a lab result file.

	Creates a MedicalCase and a LabResult in one action, then triggers
	the OCR → AI pipeline. The upload is authenticated by user or guest session.
	Authenticated users cannot upload using a guest session simultaneously.
	"""
	if ctx.user is None and ctx.guest_session_id is None:
		raise UnauthorizedError("Missing authentication or guest session.")

	if ctx.user is not None and ctx.guest_session_id is not None:
		raise ForbiddenError("Authenticated users cannot upload using a guest session.")

	# Enforce guest upload limit before accepting the file
	if ctx.user is None and ctx.guest_session_id is not None:
		await manager.can_use(ctx.guest_session_id, GuestUsageAction.UPLOAD)

	valid_media_types = {
		"image/jpeg",
		"image/png",
		"image/webp",
		"application/pdf",
	}
	if file.content_type not in valid_media_types:
		raise BadRequestError("Unsupported file type. Acceptable types are JPEG, PNG, WebP, or PDF.")

	if file.size is not None and file.size > 10 * 1024 * 1024:
		raise BadRequestError("File size must be 10MB or smaller.")

	file_contents = await file.read()
	if len(file_contents) > 10 * 1024 * 1024:
		raise BadRequestError("File size must be 10MB or smaller.")

	public_url_base = str(request.base_url).rstrip("/")
	case, lab_result = await handle_file_upload(
		lab_repo,
		case_repo,
		file_contents,
		file.filename,
		file.content_type or "application/octet-stream",
		ctx.user,
		ctx.guest_session_id,
		public_url_base,
	)

	# Increment the guest upload counter after successful upload
	if ctx.user is None and ctx.guest_session_id is not None:
		await manager.increment_upload(ctx.guest_session_id)

	return SuccessResponse(
		message="Upload received. Processing started.",
		data=UploadResponse(
			case_id=case.id,
			lab_result=LabResultResponse.model_validate(lab_result),
		),
	)


@router.post(
	"/cases/{case_id}/lab-results",
	response_model=SuccessResponse[LabResultResponse],
	status_code=status.HTTP_201_CREATED,
)
async def create(
	request: Request,
	case_id: UUID,
	current_user: CurrentUser,
	lab_repo: LabResultRepo,
	case_repo: MedicalCaseRepo,
	file: UploadFile = File(...),
) -> SuccessResponse[LabResultResponse]:
	"""Upload a new lab result file to a medical case."""
	await get_case(case_repo, case_id, user=current_user)

	valid_media_types = {
		"image/jpeg",
		"image/png",
		"image/webp",
		"application/pdf",
	}
	if file.content_type not in valid_media_types:
		raise BadRequestError("Unsupported file type. Acceptable types are JPEG, PNG, WebP, or PDF.")

	if file.size is not None and file.size > 10 * 1024 * 1024:
		raise BadRequestError("File size must be 10MB or smaller.")

	file_contents = await file.read()
	if len(file_contents) > 10 * 1024 * 1024:
		raise BadRequestError("File size must be 10MB or smaller.")

	public_url_base = str(request.base_url).rstrip("/")
	result = await add_file_to_case(
		lab_repo,
		case_repo,
		case_id,
		file_contents,
		file.filename,
		file.content_type or "application/octet-stream",
		public_url_base,
	)
	return SuccessResponse(
		message="Lab result created.",
		data=LabResultResponse.model_validate(result),
	)


@router.get(
	"/cases/{case_id}/lab-results",
	response_model=SuccessResponse[list[LabResultResponse]],
)
async def list_for_case(
	case_id: UUID,
	ctx: SessionContextDep,
	manager: GuestSessionManagerDep,
	lab_repo: LabResultRepo,
	case_repo: MedicalCaseRepo,
	offset: int = Query(0, ge=0),
	limit: int = Query(50, ge=1, le=100),
) -> SuccessResponse[list[LabResultResponse]]:
	"""List lab results for a medical case."""
	await get_case(
		case_repo,
		case_id,
		user=ctx.user,
		guest_session_id=ctx.guest_session_id,
		manager=manager,
	)
	results = await list_lab_results_for_case(lab_repo, case_id, offset=offset, limit=limit)
	return SuccessResponse(
		message="OK",
		data=[LabResultResponse.model_validate(r) for r in results],
	)


@router.get(
	"/cases/{case_id}/lab-results/{result_id}",
	response_model=SuccessResponse[LabResultResponse],
)
async def retrieve(
	case_id: UUID,
	result_id: UUID,
	ctx: SessionContextDep,
	manager: GuestSessionManagerDep,
	lab_repo: LabResultRepo,
	case_repo: MedicalCaseRepo,
) -> SuccessResponse[LabResultResponse]:
	"""Retrieve a single lab result."""
	await get_case(
		case_repo,
		case_id,
		user=ctx.user,
		guest_session_id=ctx.guest_session_id,
		manager=manager,
	)
	result = await get_lab_result(lab_repo, result_id)
	return SuccessResponse(
		message="OK",
		data=LabResultResponse.model_validate(result),
	)
