from uuid import UUID

from app.core.exceptions import NotFoundError, UnauthorizedError
from app.models.chat import Chat, SenderType
from app.models.user import User
from app.repositories.chat import ChatRepository
from app.repositories.medical_case import MedicalCaseRepository
from app.schemas.chat import ChatCreate
from app.services.guest import to_guest_session_uuid
from app.services.guest_sessions import GuestSessionManager, GuestUsageAction
from app.services.medical_case import get_case


async def send_message(
	chat_repo: ChatRepository,
	case_repo: MedicalCaseRepository,
	payload: ChatCreate,
	*,
	user: User | None = None,
	guest_session_id: str | None = None,
	manager: GuestSessionManager | None = None,
) -> Chat:
	"""Create a new chat message within a medical case."""
	await get_case(
		case_repo,
		payload.medical_case_id,
		user=user,
		guest_session_id=guest_session_id,
		manager=manager,
	)

	if user is None and payload.sender_type == SenderType.PATIENT:
		if not guest_session_id or manager is None:
			raise UnauthorizedError("Missing guest session.")
		await manager.can_use(to_guest_session_uuid(guest_session_id), GuestUsageAction.CHAT)

	message = Chat(
		user_id=user.id if user else None,
		medical_case_id=payload.medical_case_id,
		sender_type=payload.sender_type,
		content=payload.content,
	)
	chat_repo.add(message)
	await chat_repo.commit()
	await chat_repo.refresh(message)

	if user is None and payload.sender_type == SenderType.PATIENT and manager is not None and guest_session_id:
		await manager.increment_chat(to_guest_session_uuid(guest_session_id))

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
