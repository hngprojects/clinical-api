"""Guest upload quota applies only after successful pipeline; failures allow retry."""

from __future__ import annotations

import uuid
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from httpx import AsyncClient
from sqlalchemy import select

from app.db.session import AsyncSessionLocal
from app.models.guest_session import GuestSession
from app.models.medical_case import MedicalCase
from app.services.guest import create_guest_session
from app.services.guest_upload import complete_guest_upload_quota, purge_guest_failed_upload

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


async def _guest_upload_count(guest_session_id: str) -> int:
	async with AsyncSessionLocal() as session:
		row = await session.scalar(select(GuestSession.upload_count).where(GuestSession.id == uuid.UUID(guest_session_id)))
		return int(row or 0)


async def test_guest_upload_does_not_increment_until_pipeline_success(client: AsyncClient) -> None:
	guest = await create_guest_session()

	with (
		patch(PIPELINE_TASK, MagicMock()),
		patch(STORAGE_MOCK, new_callable=AsyncMock, return_value=_FAKE_METADATA),
	):
		response = await client.post(
			f"{API}/upload",
			files={"file": _FAKE_FILE},
			headers={GUEST_HEADER: guest.guest_session_id},
		)

	assert response.status_code == 201
	assert await _guest_upload_count(guest.guest_session_id) == 0


async def test_guest_concurrent_upload_returns_409(client: AsyncClient) -> None:
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


async def test_guest_quota_after_pipeline_success_blocks_second_upload(client: AsyncClient) -> None:
	guest = await create_guest_session()
	case_id: uuid.UUID | None = None

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
		case_id = uuid.UUID(first.json()["data"]["case_id"])

	await complete_guest_upload_quota(case_id)
	assert await _guest_upload_count(guest.guest_session_id) == 1

	with (
		patch(PIPELINE_TASK, MagicMock()),
		patch(STORAGE_MOCK, new_callable=AsyncMock, return_value=_FAKE_METADATA),
	):
		second = await client.post(
			f"{API}/upload",
			files={"file": _FAKE_FILE},
			headers={GUEST_HEADER: guest.guest_session_id},
		)

	assert second.status_code == 403


async def test_guest_can_retry_upload_after_pipeline_failure(client: AsyncClient) -> None:
	guest = await create_guest_session()
	case_id: uuid.UUID | None = None

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
		case_id = uuid.UUID(first.json()["data"]["case_id"])

	await purge_guest_failed_upload(case_id)

	assert await _guest_upload_count(guest.guest_session_id) == 0

	with (
		patch(PIPELINE_TASK, MagicMock()),
		patch(STORAGE_MOCK, new_callable=AsyncMock, return_value=_FAKE_METADATA),
	):
		retry = await client.post(
			f"{API}/upload",
			files={"file": _FAKE_FILE},
			headers={GUEST_HEADER: guest.guest_session_id},
		)

	assert retry.status_code == 201

	async with AsyncSessionLocal() as session:
		remaining = await session.scalar(select(MedicalCase.id).where(MedicalCase.id == case_id))
	assert remaining is None
