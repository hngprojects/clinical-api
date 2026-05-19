from uuid import UUID

from fastapi import APIRouter, Query, status

from app.api.deps import ChatRepo, GuestSessionManagerDep, MedicalCaseRepo, SessionContextDep
from app.core.responses import SuccessResponse
from app.schemas.chat import ChatCreate, ChatResponse
from app.services.chat import list_messages_for_case, send_message
from app.services.medical_case import get_case

router = APIRouter(prefix="/cases/{case_id}/chat", tags=["chat"])


@router.post(
	"",
	response_model=SuccessResponse[ChatResponse],
	status_code=status.HTTP_201_CREATED,
)
async def create_message(
	case_id: UUID,
	payload: ChatCreate,
	ctx: SessionContextDep,
	manager: GuestSessionManagerDep,
	chat_repo: ChatRepo,
	case_repo: MedicalCaseRepo,
) -> SuccessResponse[ChatResponse]:
	"""Send a chat message in a medical case."""
	payload.medical_case_id = case_id
	msg = await send_message(
		chat_repo,
		case_repo,
		payload,
		user=ctx.user,
		guest_session=ctx.guest_session,
		manager=manager,
	)
	return SuccessResponse(
		message="Message sent.",
		data=ChatResponse.model_validate(msg),
	)


@router.get(
	"",
	response_model=SuccessResponse[list[ChatResponse]],
)
async def list_messages(
	case_id: UUID,
	ctx: SessionContextDep,
	manager: GuestSessionManagerDep,
	chat_repo: ChatRepo,
	case_repo: MedicalCaseRepo,
	offset: int = Query(0, ge=0),
	limit: int = Query(50, ge=1, le=100),
) -> SuccessResponse[list[ChatResponse]]:
	"""List chat messages for a medical case (chronological)."""
	await get_case(
		case_repo,
		case_id,
		user=ctx.user,
		guest_session=ctx.guest_session,
		manager=manager,
	)
	messages = await list_messages_for_case(chat_repo, case_id, offset=offset, limit=limit)
	return SuccessResponse(
		message="OK",
		data=[ChatResponse.model_validate(m) for m in messages],
	)
