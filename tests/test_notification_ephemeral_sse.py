from __future__ import annotations

import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest

from app.api.v1.endpoints.notification import _stream_notifications
from app.services.websocket import ConnectionRegistry


pytestmark = pytest.mark.asyncio(loop_scope="session")


async def test_stream_notifications_forwards_ephemeral_events_without_db_touch() -> None:
	current_user = SimpleNamespace(id=uuid4())
	connection_registry = ConnectionRegistry()
	request = SimpleNamespace(is_disconnected=AsyncMock(return_value=False))
	notif_repo = MagicMock()
	case_id = uuid4()
	lab_result_id = uuid4()
	ephemeral_payload = {
		"event": "ocr_completed",
		"case_id": str(case_id),
		"lab_result_id": str(lab_result_id),
		"stage": "ocr",
		"status": "complete",
		"progress": 60,
		"message": "Text extraction completed successfully",
		"timestamp": "2026-05-23T12:00:00Z",
	}

	async def _event_stream():
		yield {
			"type": "ocr_completed",
			"payload": {"data": ephemeral_payload},
		}

	class _FakeBus:
		def __init__(self) -> None:
			self.subscribe = MagicMock(return_value=_event_stream())

	fake_bus = _FakeBus()

	frames: list[bytes] = []
	async for frame in _stream_notifications(request, fake_bus, notif_repo, current_user, connection_registry):
		frames.append(frame)

	assert fake_bus.subscribe.called
	fake_bus.subscribe.assert_called_once_with(current_user.id)
	assert len(frames) == 1

	text = frames[0].decode("utf-8")
	assert text.startswith("id: ")
	assert "event: ocr_completed\n" in text
	data_line = next(line for line in text.splitlines() if line.startswith("data: "))
	parsed = json.loads(data_line.removeprefix("data: "))
	assert parsed == ephemeral_payload
	assert connection_registry.get_stream_count(current_user.id) == 0