from uuid import UUID

from fastapi import HTTPException, status

from app.core.exceptions import ConflictError, ForbiddenError, NotFoundError
from app.models.chat import Chat, SenderType
from app.models.user import User
from app.repositories.ai_interpretation import AIInterpretationRepository
from app.repositories.chat import ChatRepository
from app.repositories.lab_result import LabResultRepository
from app.repositories.medical_case import MedicalCaseRepository
from app.schemas.chat import ChatAsk
from app.services.ai import MEDICAL_DISCLAIMER, ChatError, generate_chat_response
from app.services.chat_context import build_case_context, chats_to_messages
from app.services.chat_tokens import truncate_history


async def send_message(
	chat_repo: ChatRepository,
	case_repo: MedicalCaseRepository,
	lab_repo: LabResultRepository,
	interp_repo: AIInterpretationRepository,
	case_id: UUID,
	payload: ChatAsk,
	*,
	user: User | None = None,
	guest_session_id: str | None = None,
) -> tuple[Chat, Chat]:
	"""Save the patient message, generate an AI reply, and return both."""
	text = payload.text.strip()

	case = await case_repo.get_by_id(case_id)
	if case is None:
		raise NotFoundError("Medical case not found.")
	if user is not None and case.user_id != user.id:
		raise ForbiddenError("You do not have access to this case.")
	if user is None and guest_session_id is not None and case.guest_session_id != guest_session_id:
		raise ForbiddenError("You do not have access to this case.")

	lab_result = await lab_repo.get_latest_with_extracted_values(case_id)
	if lab_result is None or not lab_result.extracted_values:
		raise ConflictError("Lab results are not ready for chat yet.")

	interpretation = await interp_repo.get_latest_for_case(case_id)
	interpretation_summary = interpretation.summary if interpretation else None

	patient_message = Chat(
		user_id=user.id if user else None,
		medical_case_id=case_id,
		sender_type=SenderType.PATIENT,
		content={"text": text},
	)
	chat_repo.add(patient_message)
	await chat_repo.commit()
	await chat_repo.refresh(patient_message)

	case_context = build_case_context(lab_result.extracted_values, interpretation_summary)
	all_chats = await chat_repo.list_by_case(case_id, limit=500)
	history_messages = chats_to_messages(all_chats)
	truncated_messages = truncate_history(case_context, history_messages)

	try:
		reply_text = await generate_chat_response(
			case_context=case_context,
			messages=truncated_messages,
		)
	except ChatError as exc:
		raise HTTPException(
			status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
			detail="Unable to generate a reply right now. Please try again later.",
		) from exc

	ai_message = Chat(
		user_id=None,
		medical_case_id=case_id,
		sender_type=SenderType.AI,
		content={"text": reply_text, "disclaimer": MEDICAL_DISCLAIMER},
	)
	chat_repo.add(ai_message)
	await chat_repo.commit()
	await chat_repo.refresh(ai_message)

	return patient_message, ai_message


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
