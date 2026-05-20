import json
import asyncio
from uuid import uuid4

from app.api.v1.endpoints.notification import _format_sse
from app.models.notification import NotificationType
from app.models.user import User
from app.services.notification import get_notification_preferences


def test_notification_sse_formatter_uses_uuid_and_json_payload():
	notification_id = uuid4()
	payload = {
		"id": str(notification_id),
		"title": "Interpretation ready",
		"message": {"text": "Your result is ready."},
		"data": {"case_id": "case-123"},
		"delivered_at": None,
	}

	frame = _format_sse(NotificationType.INTERPRETATION_READY.value, payload, notification_id)
	text = frame.decode("utf-8")

	assert text.startswith(f"id: {notification_id}\n")
	assert f"event: {NotificationType.INTERPRETATION_READY.value}\n" in text
	data_line = next(line for line in text.splitlines() if line.startswith("data: "))
	parsed = json.loads(data_line.removeprefix("data: "))
	assert parsed == payload
	assert text.endswith("\n\n")


def test_notification_preferences_service_reads_user_flag():
	user = User(
		id=uuid4(),
		email="notify@example.com",
		first_name="Notify",
		last_name="User",
		role="patient",
		is_active=True,
		is_email_verified=True,
		notify_on_complete=False,
	)

	result = asyncio.run(get_notification_preferences(user))
	assert result == {"notify_on_complete": False}