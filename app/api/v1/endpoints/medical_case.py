from uuid import UUID

from fastapi import APIRouter, Query, status

from app.api.deps import (
	AIInterpretationRepo,
	ChatRepo,
	CurrentUser,
	GuestSessionManagerDep,
	LabResultRepo,
	MedicalCaseRepo,
	PipelineAuditLogRepo,
	SessionContextDep,
)
from app.core.responses import SuccessResponse
from app.schemas.ai_interpretation import AIInterpretationResponse
from app.schemas.chat import ChatResponse
from app.schemas.lab_result import LabResultResponse
from app.schemas.medical_case import MedicalCaseDetailResponse, MedicalCaseResponse, MedicalCaseUpdate
from app.schemas.pipeline_audit_log import PipelineAuditLogResponse
from app.services.medical_case import (
	complete_case,
	create_case_for_user,
	get_case,
	get_case_full,
	get_case_title,
	list_cases_for_user,
	update_case,
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
	lab_repo: LabResultRepo,
	offset: int = Query(0, ge=0),
	limit: int = Query(50, ge=1, le=100),
) -> SuccessResponse[list[MedicalCaseResponse]]:
	"""List the authenticated user's medical cases."""
	cases, _total = await list_cases_for_user(
		case_repo,
		current_user.id,
		offset=offset,
		limit=limit,
	)
	responses = [
		MedicalCaseResponse.model_validate(case).model_copy(
			update={"title": await get_case_title(case, lab_repo)},
		)
		for case in cases
	]
	return SuccessResponse(
		message="OK",
		data=responses,
	)


@router.patch(
	"/{case_id}",
	response_model=SuccessResponse[MedicalCaseResponse],
	status_code=status.HTTP_200_OK,
)
async def update(
	case_id: UUID,
	payload: dict,
	current_user: CurrentUser,
	case_repo: MedicalCaseRepo,
) -> SuccessResponse[MedicalCaseResponse]:
	"""Update a medical case title only.

	This endpoint intentionally ignores any `status` or other keys in the
	payload — only `title` is accepted. Status updates are controlled by the
	AI/pipeline and should not be set here.
	"""
	case = await get_case(case_repo, case_id, user=current_user)

	title = payload.get("title") if isinstance(payload, dict) else None
	if title is not None:
		if not isinstance(title, str):
			title = None
		else:
			title = title.strip() or None
	case.title = title
	await case_repo.commit()
	await case_repo.refresh(case)

	response = MedicalCaseResponse.model_validate(case)
	return SuccessResponse(message="Medical case updated.", data=response)


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
		guest_session_id=ctx.guest_session_id,
		manager=manager,
	)
	return SuccessResponse(
		message="OK",
		data=MedicalCaseDetailResponse(
			case=MedicalCaseResponse.model_validate(detail.case).model_copy(
				update={"title": await get_case_title(detail.case, lab_repo)},
			),
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
	lab_repo: LabResultRepo,
) -> SuccessResponse[MedicalCaseResponse]:
	"""Retrieve a single medical case (ownership enforced by user or guest_session_id)."""
	case = await get_case(
		case_repo,
		case_id,
		user=ctx.user,
		guest_session_id=ctx.guest_session_id,
		manager=manager,
	)
	return SuccessResponse(
		message="OK",
		data=MedicalCaseResponse.model_validate(case).model_copy(
			update={"title": await get_case_title(case, lab_repo)},
		),
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


@router.get(
	"/{case_id}/pipeline-log",
	response_model=SuccessResponse[list[PipelineAuditLogResponse]],
)
async def pipeline_log(
	case_id: UUID,
	current_user: CurrentUser,
	case_repo: MedicalCaseRepo,
	lab_repo: LabResultRepo,
	audit_repo: PipelineAuditLogRepo,
) -> SuccessResponse[list[PipelineAuditLogResponse]]:
	"""Return pipeline audit log entries for all lab results in a case."""
	case = await get_case(case_repo, case_id, user=current_user)
	lab_results = await lab_repo.list_by_case(case.id)
	entries: list[PipelineAuditLogResponse] = []
	for lr in lab_results:
		logs = await audit_repo.list_by_lab_result(lr.id)
		entries.extend(PipelineAuditLogResponse.model_validate(log) for log in logs)
	entries.sort(key=lambda e: e.created_at)
	return SuccessResponse(message="OK", data=entries)
