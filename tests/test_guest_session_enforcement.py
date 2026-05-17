"""Phase 3: guest session enforcement on upload, case access, and chat limits."""

from __future__ import annotations

import uuid
from unittest.mock import MagicMock, patch

import pytest
from httpx import AsyncClient

from app.services.guest import create_guest_session
from tests.fakes.redis import FakeRedis

pytestmark = pytest.mark.asyncio(loop_scope="session")

API = "/api/v1"
GUEST_HEADER = "X-Guest-Session-Id"
PIPELINE_TASK = "app.tasks.pipeline.run_lab_result_pipeline"

_UPLOAD_PAYLOAD = {
	"file": {
		"name": "panel.jpg",
		"url": "https://storage.example.com/panel.jpg",
	},
}


@pytest.fixture
def fake_redis() -> FakeRedis:
	return FakeRedis()


@pytest.fixture(autouse=True)
def patch_redis(fake_redis: FakeRedis, monkeypatch: pytest.MonkeyPatch) -> None:
	async def _get_redis() -> FakeRedis:
		return fake_redis

	monkeypatch.setattr("app.core.redis_client.get_redis", _get_redis)
	monkeypatch.setattr("app.services.guest.get_redis", _get_redis)


async def test_guest_upload_with_valid_session_returns_201(
	client: AsyncClient,
	fake_redis: FakeRedis,
) -> None:
	session = await create_guest_session(redis=fake_redis)
	mock_task = MagicMock()

	with patch(PIPELINE_TASK, mock_task):
		response = await client.post(
			f"{API}/upload",
			json={**_UPLOAD_PAYLOAD, "guest_session_id": session.guest_session_id},
		)

	assert response.status_code == 201
	body = response.json()["data"]
	assert body["case_id"]
	assert body["lab_result"]["ocr_status"] == "pending"
	mock_task.delay.assert_called_once()


async def test_guest_upload_without_session_returns_401(client: AsyncClient) -> None:
	response = await client.post(f"{API}/upload", json=_UPLOAD_PAYLOAD)

	assert response.status_code == 401


async def test_guest_case_access_wrong_session_returns_403(
	client: AsyncClient,
	fake_redis: FakeRedis,
) -> None:
	owner = await create_guest_session(redis=fake_redis)
	other = await create_guest_session(redis=fake_redis)
	mock_task = MagicMock()

	with patch(PIPELINE_TASK, mock_task):
		upload = await client.post(
			f"{API}/upload",
			json={**_UPLOAD_PAYLOAD, "guest_session_id": owner.guest_session_id},
		)
	case_id = upload.json()["data"]["case_id"]

	response = await client.get(
		f"{API}/cases/{case_id}",
		headers={GUEST_HEADER: other.guest_session_id},
	)

	assert response.status_code == 403


async def test_guest_fourth_patient_message_returns_403(
	client: AsyncClient,
	fake_redis: FakeRedis,
) -> None:
	session = await create_guest_session(redis=fake_redis)
	mock_task = MagicMock()

	with patch(PIPELINE_TASK, mock_task):
		upload = await client.post(
			f"{API}/upload",
			json={**_UPLOAD_PAYLOAD, "guest_session_id": session.guest_session_id},
		)
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
	fake_redis: FakeRedis,
) -> None:
	session = await create_guest_session(redis=fake_redis)
	mock_task = MagicMock()

	with patch(PIPELINE_TASK, mock_task):
		response = await client.post(
			f"{API}/upload",
			json={**_UPLOAD_PAYLOAD, "guest_session_id": session.guest_session_id},
			headers=auth_headers,
		)

	assert response.status_code == 403
