from uuid import UUID

from fastapi import APIRouter, Query, status

from app.api.deps import (
	AIInterpretationRepo,
	ChatRepo,
	GuestSessionId,
	LabResultRepo,
	MedicalCaseRepo,
	OptionalUser,
)
from app.core.responses import SuccessResponse
from app.schemas.chat import ChatAsk, ChatExchangeResponse, ChatResponse
from app.services.chat import list_messages_for_case, send_message
from app.services.medical_case import get_case

router = APIRouter(prefix="/cases/{case_id}/chat", tags=["chat"])


@router.post(
	"",
	response_model=SuccessResponse[ChatExchangeResponse],
	status_code=status.HTTP_201_CREATED,
)
async def create_message(
	case_id: UUID,
	payload: ChatAsk,
	current_user: OptionalUser,
	guest_session_id: GuestSessionId,
	chat_repo: ChatRepo,
	case_repo: MedicalCaseRepo,
	lab_repo: LabResultRepo,
	interp_repo: AIInterpretationRepo,
) -> SuccessResponse[ChatExchangeResponse]:
	"""Send a chat message and receive an AI reply."""
	user_message, ai_message = await send_message(
		chat_repo,
		case_repo,
		lab_repo,
		interp_repo,
		case_id,
		payload,
		user=current_user,
		guest_session_id=guest_session_id,
	)
	return SuccessResponse(
		message="Message sent.",
		data=ChatExchangeResponse(
			user_message=ChatResponse.model_validate(user_message),
			ai_message=ChatResponse.model_validate(ai_message),
		),
	)


@router.get(
	"",
	response_model=SuccessResponse[list[ChatResponse]],
)
async def list_messages(
	case_id: UUID,
	current_user: OptionalUser,
	guest_session_id: GuestSessionId,
	chat_repo: ChatRepo,
	case_repo: MedicalCaseRepo,
	offset: int = Query(0, ge=0),
	limit: int = Query(50, ge=1, le=100),
) -> SuccessResponse[list[ChatResponse]]:
	"""List chat messages for a medical case (chronological)."""
	await get_case(case_repo, case_id, user=current_user, guest_session_id=guest_session_id)
	messages = await list_messages_for_case(chat_repo, case_id, offset=offset, limit=limit)
	return SuccessResponse(
		message="OK",
		data=[ChatResponse.model_validate(m) for m in messages],
	)
