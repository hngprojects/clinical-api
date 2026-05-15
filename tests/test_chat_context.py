import uuid
from datetime import datetime, timezone

from app.models.chat import Chat, SenderType
from app.services.chat_context import build_case_context, chats_to_messages, format_lab_values

EXTRACTED = {
	"tests": [
		{"name": "Haemoglobin", "value": "11.2", "unit": "g/dL", "reference_range": "12.0–17.5"},
	]
}


def test_format_lab_values_includes_metric():
	formatted = format_lab_values(EXTRACTED)
	assert "Haemoglobin" in formatted
	assert "11.2" in formatted


def test_build_case_context_includes_interpretation():
	context = build_case_context(EXTRACTED, "Low haemoglobin may indicate anaemia.")
	assert "Haemoglobin" in context
	assert "Low haemoglobin" in context


def test_chats_to_messages_maps_sender_types():
	now = datetime.now(timezone.utc)
	chats = [
		Chat(
			id=uuid.uuid4(),
			medical_case_id=uuid.uuid4(),
			sender_type=SenderType.PATIENT,
			content={"text": "What does this mean?"},
			sent_at=now,
		),
		Chat(
			id=uuid.uuid4(),
			medical_case_id=uuid.uuid4(),
			sender_type=SenderType.AI,
			content={"text": "Your haemoglobin is below range."},
			sent_at=now,
		),
	]
	messages = chats_to_messages(chats)
	assert messages == [
		{"role": "user", "content": "What does this mean?"},
		{"role": "assistant", "content": "Your haemoglobin is below range."},
	]
