"""PR3: guest session usage limits (chat_count / upload_count)."""

from __future__ import annotations

import uuid
from unittest.mock import MagicMock, patch

import pytest
from httpx import AsyncClient

from app.services.guest import create_guest_session

pytestmark = pytest.mark.asyncio(loop_scope="session")

API = "/api/v1"
PIPELINE_TASK = "app.tasks.pipeline.run_lab_result_pipeline"

_UPLOAD = {
	"file": {
		"name": "panel.jpg",
		"url": "https://storage.example.com/panel.jpg",
	},
}


async def test_second_guest_upload_returns_403(client: AsyncClient) -> None:
	guest = await create_guest_session()
	mock_task = MagicMock()

	with patch(PIPELINE_TASK, mock_task):
		first = await client.post(
			f"{API}/upload",
			json={**_UPLOAD, "guest_session_id": guest.guest_session_id},
		)
		assert first.status_code == 201

		second = await client.post(
			f"{API}/upload",
			json={**_UPLOAD, "guest_session_id": guest.guest_session_id},
		)

	assert second.status_code == 403
	assert "upload" in second.json()["message"].lower()
