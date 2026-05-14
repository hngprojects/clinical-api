from uuid import UUID

from fastapi import APIRouter, Query, status

from app.api.deps import CurrentUser, GuestSessionId, LabResultRepo, MedicalCaseRepo, OptionalUser
from app.core.responses import SuccessResponse
from app.schemas.lab_result import LabResultCreate, LabResultResponse, UploadRequest, UploadResponse
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
	lab_repo: LabResultRepo,
	case_repo: MedicalCaseRepo,
) -> SuccessResponse[UploadResponse]:
	"""Upload a lab result file.

	Creates a MedicalCase and a LabResult in one action, then triggers
	the OCR → AI pipeline. This is the primary upload path — the frontend
	sends one request and polls GET /cases/{case_id}/interpretations/latest
	for the result.
	"""
	case, lab_result = await upload_lab_result(lab_repo, case_repo, payload, None)
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
	current_user: OptionalUser,
	guest_session_id: GuestSessionId,
	lab_repo: LabResultRepo,
	case_repo: MedicalCaseRepo,
	offset: int = Query(0, ge=0),
	limit: int = Query(50, ge=1, le=100),
) -> SuccessResponse[list[LabResultResponse]]:
	"""List lab results for a medical case."""
	await get_case(case_repo, case_id, user=current_user, guest_session_id=guest_session_id)
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
	current_user: OptionalUser,
	guest_session_id: GuestSessionId,
	lab_repo: LabResultRepo,
	case_repo: MedicalCaseRepo,
) -> SuccessResponse[LabResultResponse]:
	"""Retrieve a single lab result."""
	await get_case(case_repo, case_id, user=current_user, guest_session_id=guest_session_id)
	result = await get_lab_result(lab_repo, result_id)
	return SuccessResponse(
		message="OK",
		data=LabResultResponse.model_validate(result),
	)
