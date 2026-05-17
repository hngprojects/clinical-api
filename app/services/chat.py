from uuid import UUID

from app.core.config import get_settings
from app.core.exceptions import ForbiddenError, NotFoundError
from app.models.chat import Chat, SenderType
from app.models.user import User
from app.repositories.chat import ChatRepository
from app.repositories.medical_case import MedicalCaseRepository
from app.schemas.chat import ChatCreate
from app.services.medical_case import get_case


async def send_message(
	chat_repo: ChatRepository,
	case_repo: MedicalCaseRepository,
	payload: ChatCreate,
	*,
	user: User | None = None,
	guest_session_id: str | None = None,
) -> Chat:
	"""Create a new chat message within a medical case."""
	case = await get_case(
		case_repo,
		payload.medical_case_id,
		user=user,
		guest_session_id=guest_session_id,
	)

	if user is None and payload.sender_type == SenderType.PATIENT:
		patient_messages = await chat_repo.count_patient_messages_by_case(case.id)
		if patient_messages >= get_settings().GUEST_CHAT_MESSAGE_LIMIT:
			raise ForbiddenError("Guest message limit reached. Please sign up to continue chatting.")

	message = Chat(
		user_id=payload.user_id or (user.id if user else None),
		medical_case_id=payload.medical_case_id,
		sender_type=payload.sender_type,
		content=payload.content,
	)
	chat_repo.add(message)
	await chat_repo.commit()
	await chat_repo.refresh(message)
	return message


async def get_message(
	chat_repo: ChatRepository,
	message_id: UUID,
) -> Chat:
	msg = await chat_repo.get_by_id(message_id)
	if msg is None:
		raise NotFoundError("Chat message not found.")
	return msg


async def list_messages_for_case(
	chat_repo: ChatRepository,
	medical_case_id: UUID,
	*,
	offset: int = 0,
	limit: int = 50,
) -> list[Chat]:
	return await chat_repo.list_by_case(medical_case_id, offset=offset, limit=limit)
