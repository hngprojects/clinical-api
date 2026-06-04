"""Phase 3: guest session enforcement on upload, case access, and chat limits."""

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

_FAKE_FILE = ("panel.jpg", b"fake-image-bytes", "image/jpeg")
_FAKE_METADATA = {
	"filename": "panel.jpg",
	"mime_type": "image/jpeg",
	"file_size": 16,
	"file_url": "http://testserver/media/fake-uuid.jpg",
}


async def test_guest_upload_with_valid_session_returns_201(client: AsyncClient) -> None:
	session = await create_guest_session()

	with (
		patch(PIPELINE_TASK, MagicMock()),
		patch(STORAGE_MOCK, new_callable=AsyncMock, return_value=_FAKE_METADATA),
	):
		response = await client.post(
			f"{API}/upload",
			files={"file": _FAKE_FILE},
			headers={GUEST_HEADER: session.guest_session_id},
		)

	assert response.status_code == 201
	body = response.json()["data"]
	assert body["case_id"]
	assert body["lab_result"]["ocr_status"] == "pending"


async def test_guest_upload_without_session_returns_401(client: AsyncClient) -> None:
	with patch(STORAGE_MOCK, new_callable=AsyncMock, return_value=_FAKE_METADATA):
		response = await client.post(
			f"{API}/upload",
			files={"file": _FAKE_FILE},
		)

	assert response.status_code == 401


async def test_guest_case_access_wrong_session_returns_403(client: AsyncClient) -> None:
	owner = await create_guest_session()
	other = await create_guest_session()

	with (
		patch(PIPELINE_TASK, MagicMock()),
		patch(STORAGE_MOCK, new_callable=AsyncMock, return_value=_FAKE_METADATA),
	):
		upload = await client.post(
			f"{API}/upload",
			files={"file": _FAKE_FILE},
			headers={GUEST_HEADER: owner.guest_session_id},
		)
	assert upload.status_code == 201
	case_id = upload.json()["data"]["case_id"]

	response = await client.get(
		f"{API}/cases/{case_id}",
		headers={GUEST_HEADER: other.guest_session_id},
	)

	assert response.status_code == 403


async def test_guest_fourth_patient_message_returns_403(client: AsyncClient) -> None:
	session = await create_guest_session()

	with (
		patch(PIPELINE_TASK, MagicMock()),
		patch(STORAGE_MOCK, new_callable=AsyncMock, return_value=_FAKE_METADATA),
	):
		upload = await client.post(
			f"{API}/upload",
			files={"file": _FAKE_FILE},
			headers={GUEST_HEADER: session.guest_session_id},
		)
	assert upload.status_code == 201
	case_id = upload.json()["data"]["case_id"]
	headers = {GUEST_HEADER: session.guest_session_id}

	for i in range(3):
		resp = await client.post(
			f"{API}/cases/{case_id}/chat",
			json={
				"sender_type": "patient",
				"content": {"text": f"Question {i + 1}"},
				"medical_case_id": case_id,
			},
			headers=headers,
		)
		assert resp.status_code == 201, resp.text

	fourth = await client.post(
		f"{API}/cases/{case_id}/chat",
		json={
			"sender_type": "patient",
			"content": {"text": "Question 4"},
			"medical_case_id": case_id,
		},
		headers=headers,
	)

	assert fourth.status_code == 403
	assert "limit" in fourth.json()["message"].lower()


async def test_authenticated_upload_ignores_guest_header(
	client: AsyncClient,
	auth_headers: dict[str, str],
) -> None:
	session = await create_guest_session()

	with (
		patch(PIPELINE_TASK, MagicMock()),
		patch(STORAGE_MOCK, new_callable=AsyncMock, return_value=_FAKE_METADATA),
	):
		response = await client.post(
			f"{API}/upload",
			files={"file": _FAKE_FILE},
			headers={**auth_headers, GUEST_HEADER: session.guest_session_id},
		)

	assert response.status_code == 403
