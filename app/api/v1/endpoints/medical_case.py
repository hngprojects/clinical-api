from uuid import UUID

from fastapi import APIRouter, Query, status

from app.api.deps import (
	AIInterpretationRepo,
	ChatRepo,
	CurrentUser,
	GuestSessionManagerDep,
	LabResultRepo,
	MedicalCaseRepo,
	SessionContextDep,
)
from app.core.responses import SuccessResponse
from app.schemas.ai_interpretation import AIInterpretationResponse
from app.schemas.chat import ChatResponse
from app.schemas.lab_result import LabResultResponse
from app.schemas.medical_case import MedicalCaseDetailResponse, MedicalCaseResponse
from app.services.medical_case import (
	complete_case,
	create_case_for_user,
	get_case,
	get_case_full,
	list_cases_for_user,
)

router = APIRouter(prefix="/cases", tags=["medical-cases"])


@router.post(
	"",
	response_model=SuccessResponse[MedicalCaseResponse],
	status_code=status.HTTP_201_CREATED,
)
async def create(
	current_user: CurrentUser,
	case_repo: MedicalCaseRepo,
) -> SuccessResponse[MedicalCaseResponse]:
	"""Create a new medical case for the authenticated user."""
	case = await create_case_for_user(case_repo, current_user)
	return SuccessResponse(
		message="Medical case created.",
		data=MedicalCaseResponse.model_validate(case),
	)


@router.get(
	"",
	response_model=SuccessResponse[list[MedicalCaseResponse]],
)
async def list_mine(
	current_user: CurrentUser,
	case_repo: MedicalCaseRepo,
	offset: int = Query(0, ge=0),
	limit: int = Query(50, ge=1, le=100),
) -> SuccessResponse[list[MedicalCaseResponse]]:
	"""List the authenticated user's medical cases."""
	cases, total = await list_cases_for_user(
		case_repo,
		current_user.id,
		offset=offset,
		limit=limit,
	)
	return SuccessResponse(
		message="OK",
		data=[MedicalCaseResponse.model_validate(c) for c in cases],
	)


@router.get(
	"/{case_id}/full",
	response_model=SuccessResponse[MedicalCaseDetailResponse],
)
async def retrieve_full(
	case_id: UUID,
	ctx: SessionContextDep,
	manager: GuestSessionManagerDep,
	case_repo: MedicalCaseRepo,
	lab_repo: LabResultRepo,
	interp_repo: AIInterpretationRepo,
	chat_repo: ChatRepo,
) -> SuccessResponse[MedicalCaseDetailResponse]:
	"""Return case, lab results, latest interpretation, and chat in one response."""
	detail = await get_case_full(
		case_repo,
		lab_repo,
		interp_repo,
		chat_repo,
		case_id,
		user=ctx.user,
		guest_session=ctx.guest_session,
		manager=manager,
	)
	return SuccessResponse(
		message="OK",
		data=MedicalCaseDetailResponse(
			case=MedicalCaseResponse.model_validate(detail.case),
			lab_results=[LabResultResponse.model_validate(lr) for lr in detail.lab_results],
			interpretation=AIInterpretationResponse.model_validate(detail.interpretation)
			if detail.interpretation is not None
			else None,
			chats=[ChatResponse.model_validate(c) for c in detail.chats],
		),
	)


@router.get(
	"/{case_id}",
	response_model=SuccessResponse[MedicalCaseResponse],
)
async def retrieve(
	case_id: UUID,
	ctx: SessionContextDep,
	manager: GuestSessionManagerDep,
	case_repo: MedicalCaseRepo,
) -> SuccessResponse[MedicalCaseResponse]:
	"""Retrieve a single medical case (ownership enforced by user or guest_session_id)."""
	case = await get_case(
		case_repo,
		case_id,
		user=ctx.user,
		guest_session=ctx.guest_session,
		manager=manager,
	)
	return SuccessResponse(
		message="OK",
		data=MedicalCaseResponse.model_validate(case),
	)


@router.post(
	"/{case_id}/complete",
	response_model=SuccessResponse[MedicalCaseResponse],
)
async def mark_complete(
	case_id: UUID,
	current_user: CurrentUser,
	case_repo: MedicalCaseRepo,
) -> SuccessResponse[MedicalCaseResponse]:
	"""Mark a medical case as complete."""
	case = await complete_case(case_repo, case_id, user=current_user)
	return SuccessResponse(
		message="Case marked as complete.",
		data=MedicalCaseResponse.model_validate(case),
	)
