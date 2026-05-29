"""PR3: guest session usage limits (chat_count / upload_count)."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from httpx import AsyncClient

from app.services.guest import create_guest_session

pytestmark = pytest.mark.asyncio(loop_scope="session")

API = "/api/v1"
GUEST_HEADER = "X-Guest-Session-Id"
PIPELINE_TASK = "app.tasks.pipeline.run_lab_result_pipeline"
STORAGE_MOCK = "app.services.storage.upload_medical_file"
GUEST_HEADER = "X-Guest-Session-Id"

_FAKE_FILE = ("panel.jpg", b"fake-image-bytes", "image/jpeg")
_FAKE_METADATA = {
	"filename": "panel.jpg",
	"file_type": "image/jpeg",
	"file_size": 16,
	"file_url": "http://testserver/media/fake-uuid.jpg",
}


async def test_second_guest_upload_while_processing_returns_409(client: AsyncClient) -> None:
	"""While the first upload is in-flight (lock held), a second upload is rejected."""
	guest = await create_guest_session()

	with (
		patch(PIPELINE_TASK, MagicMock()),
		patch(STORAGE_MOCK, new_callable=AsyncMock, return_value=_FAKE_METADATA),
	):
		first = await client.post(
			f"{API}/upload",
			files={"file": _FAKE_FILE},
			headers={GUEST_HEADER: guest.guest_session_id},
		)
		assert first.status_code == 201

		second = await client.post(
			f"{API}/upload",
			files={"file": _FAKE_FILE},
			headers={GUEST_HEADER: guest.guest_session_id},
		)

	assert second.status_code == 409
