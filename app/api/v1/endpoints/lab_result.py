from uuid import UUID

from fastapi import APIRouter, Query, status

from app.api.deps import (
	CurrentUser,
	GuestSessionManagerDep,
	LabResultRepo,
	MedicalCaseRepo,
	SessionContextDep,
)
from app.core.exceptions import UnauthorizedError
from app.core.exceptions import ForbiddenError
from app.core.responses import SuccessResponse
from app.schemas.lab_result import LabResultCreate, LabResultResponse, UploadRequest, UploadResponse
from app.services.guest import normalize_guest_session_id, to_guest_session_uuid
from app.services.lab_result import (
	create_lab_result,
	get_lab_result,
	list_lab_results_for_case,
	upload_lab_result,
)
from app.services.medical_case import get_case

router = APIRouter(tags=["lab-results"])


@router.post(
	"/upload",
	response_model=SuccessResponse[UploadResponse],
	status_code=status.HTTP_201_CREATED,
)
async def upload(
	payload: UploadRequest,
	ctx: SessionContextDep,
	manager: GuestSessionManagerDep,
	lab_repo: LabResultRepo,
	case_repo: MedicalCaseRepo,
) -> SuccessResponse[UploadResponse]:
	"""Upload a lab result reference.

	Creates a MedicalCase and a LabResult in one action, then triggers
	the OCR -> AI pipeline. The upload is authenticated by user or guest session.
	"""
	if ctx.user is not None and payload.guest_session_id:
		raise ForbiddenError("You cannot use a guest session while authenticated.")

	guest_session = ctx.guest_session
	if guest_session is None and payload.guest_session_id:
		normalized = normalize_guest_session_id(payload.guest_session_id)
		if normalized is None:
			raise UnauthorizedError("Invalid guest session id.")
		guest_session = await manager.get(to_guest_session_uuid(normalized))
		if guest_session is None:
			raise UnauthorizedError("Guest session expired or invalid.")

	if ctx.user is None and guest_session is None:
		raise UnauthorizedError("Missing authentication or guest session.")

	case, lab_result = await upload_lab_result(
		lab_repo,
		case_repo,
		payload,
		ctx.user,
		guest_session=guest_session,
		manager=manager,
	)

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
	case_id: UUID,
	payload: LabResultCreate,
	current_user: CurrentUser,
	lab_repo: LabResultRepo,
	case_repo: MedicalCaseRepo,
) -> SuccessResponse[LabResultResponse]:
	"""Upload a new lab result to a medical case."""
	await get_case(case_repo, case_id, user=current_user)
	payload.medical_case_id = case_id
	result = await create_lab_result(lab_repo, case_repo, payload)
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
