from __future__ import annotations

import json
import logging
import uuid
from collections.abc import AsyncGenerator

from app.models.chat import Chat, SenderType
from app.repositories.ai_interpretation import AIInterpretationRepository
from app.repositories.chat import ChatRepository
from app.repositories.lab_result import LabResultRepository
from app.services import llm

logger = logging.getLogger(__name__)

# ── Token budget
# We estimate tokens roughly as characters divided by 4.
# This is not exact but is a well-known approximation for English text.
# We are not billing per token here — we just need a safety cutoff to avoid
# sending more context than the model can handle.

_CHARS_PER_TOKEN = 4
_MAX_TOKENS = 3_000


# ── Serialisation


def chat_to_wire(msg: Chat) -> dict:
	"""Convert a Chat ORM object into a plain dict safe to send over WebSocket.
	We read msg.content["text"] because Chat.content is a JSONB column stored
	as {"text": "the actual message"} — not a plain string."""
	return {
		"id": str(msg.id),
		"sender_type": msg.sender_type.value,
		"content": msg.content.get("text", ""),
		"sent_at": msg.sent_at.isoformat(),
	}


# ── System prompt


async def build_system_prompt(
	case_id: uuid.UUID,
	interp_repo: AIInterpretationRepository,
	lab_repo: LabResultRepository,
) -> str:
	"""
	Build the system prompt the AI receives before every response.
	It contains:
	- A fixed instruction telling the AI how to behave
	- The AI interpretation summary for this case (if it exists)
	- The extracted lab values from OCR (if they exist)

	This context is NEVER trimmed from the token budget — it is the foundation
	every AI response is built on. Only conversation history gets trimmed.

	If the pipeline hasn't finished yet and no interpretation exists,
	we still return a valid prompt so the chat works anyway.
	"""
	interpretation = await interp_repo.get_latest_for_case(case_id)

	summary = ""
	value_breakdown = ""

	if interpretation:
		summary = interpretation.summary or ""
		if interpretation.value_breakdown:
			value_breakdown = json.dumps(interpretation.value_breakdown, indent=2)

	# Get extracted values from the most recently completed lab result
	from sqlalchemy import select

	from app.models.lab_result import LabResult, OCRStatus

	result = await lab_repo._session.execute(
		select(LabResult)
		.where(
			LabResult.medical_case_id == case_id,
			LabResult.ocr_status == OCRStatus.COMPLETE,
		)
		.order_by(LabResult.ocr_completed_at.desc())
		.limit(1)
	)
	lab = result.scalar_one_or_none()
	extracted_values = ""
	if lab and lab.extracted_values:
		extracted_values = json.dumps(lab.extracted_values, indent=2)

	# Build the prompt in sections
	parts = [
		"You are a medical assistant helping a patient understand their lab results.",
		"Be clear, compassionate, and avoid unnecessary medical jargon.",
		"Never diagnose. Always recommend consulting a healthcare professional for medical decisions.",
		"",
	]

	if summary:
		parts += ["## AI Interpretation Summary", summary, ""]

	if value_breakdown:
		parts += ["## Value Breakdown", value_breakdown, ""]

	if extracted_values:
		parts += ["## Extracted Lab Values", extracted_values, ""]

	if not summary and not extracted_values:
		parts.append(
			"No lab results have been processed yet for this case. "
			"Answer general health questions helpfully but note you don't "
			"have specific lab data for this patient yet."
		)

	return "\n".join(parts)


# ── Token counting and history trimming


def _estimate_tokens(text: str) -> int:
	return max(1, len(text) // _CHARS_PER_TOKEN)


def _history_to_text(history: list[Chat]) -> str:
	"""Flatten conversation history to a single string for token estimation."""
	parts = []
	for msg in history:
		role = "Patient" if msg.sender_type == SenderType.PATIENT else "AI"
		parts.append(f"{role}: {msg.content.get('text', '')}")
	return "\n".join(parts)


def trim_history(
	history: list[Chat],
	system_prompt: str,
	new_user_message: str,
) -> list[Chat]:
	"""
	Trim the oldest conversation turns until everything fits within _MAX_TOKENS.
	Rules:
	- System prompt tokens are counted but never removed here
	- The new user message is included in the budget calculation
	- We always keep at least the last 2 messages (one full exchange) even
	  if they alone exceed the budget — prevents an infinite trim loop
	- We drop one message at a time from the oldest end until it fits
	"""
	system_tokens = _estimate_tokens(system_prompt)
	new_msg_tokens = _estimate_tokens(new_user_message)
	budget = _MAX_TOKENS - system_tokens - new_msg_tokens

	if budget <= 0:
		logger.warning("[chat] system prompt is very large — keeping last 2 messages only")
		return history[-2:] if len(history) >= 2 else history

	trimmed = list(history)

	while len(trimmed) > 2:
		used = _estimate_tokens(_history_to_text(trimmed))
		if used <= budget:
			break
		trimmed = trimmed[1:]  # drop the oldest message

	if len(trimmed) < len(history):
		logger.info(
			"[chat] trimmed history from %d to %d messages to fit token budget",
			len(history),
			len(trimmed),
		)

	return trimmed


# ── Message persistence


async def save_user_message(
	chat_repo: ChatRepository,
	case_id: uuid.UUID,
	user_id: uuid.UUID,
	text: str,
) -> Chat:
	"""
	Save a patient message to the database.
	Content is stored as {"text": "..."} to match the JSONB schema.
	"""
	msg = Chat(
		user_id=user_id,
		medical_case_id=case_id,
		sender_type=SenderType.PATIENT,
		content={"text": text},
	)
	chat_repo.add(msg)
	await chat_repo.commit()
	await chat_repo.refresh(msg)
	logger.debug("[chat] saved user message id=%s case=%s", msg.id, case_id)
	return msg


async def save_ai_message(
	chat_repo: ChatRepository,
	case_id: uuid.UUID,
	text: str,
) -> Chat:
	"""
	Save a completed AI response to the database.
	user_id is None because the AI is not a user.
	This runs even if the client disconnected mid-stream — we always
	persist the full response so it appears in history on reconnect.
	"""
	msg = Chat(
		user_id=None,
		medical_case_id=case_id,
		sender_type=SenderType.AI,
		content={"text": text},
	)
	chat_repo.add(msg)
	await chat_repo.commit()
	await chat_repo.refresh(msg)
	logger.debug("[chat] saved AI message id=%s case=%s", msg.id, case_id)
	return msg


# ── AI response generation


async def generate_ai_response(
	system_prompt: str,
	history: list[Chat],
	user_message: str,
) -> AsyncGenerator[str, None]:
	"""
	Trim history to fit the token budget, build the conversation prompt,
	then stream tokens from the LLM one by one.

	The caller receives each token as it arrives and sends it over the
	WebSocket immediately — this is what makes the typing effect work.

	We format the conversation as a single user-turn string because
	llm.stream_text_complete takes one system prompt and one user prompt.
	We reconstruct the conversation history inside that user prompt so
	the AI has full context of what was said before.
	"""
	trimmed = trim_history(history, system_prompt, user_message)

	# Build conversation as "Role: message" lines so the AI understands who said what in the conversation history
	conversation_parts = []
	for msg in trimmed:
		role = "Patient" if msg.sender_type == SenderType.PATIENT else "AI"
		conversation_parts.append(f"{role}: {msg.content.get('text', '')}")

	# Add the new message at the end and prompt the AI to continue
	conversation_parts.append(f"Patient: {user_message}")
	conversation_parts.append("AI:")

	user_prompt = "\n".join(conversation_parts)

	async for token in llm.stream_text_complete(system_prompt, user_prompt):
		yield token
