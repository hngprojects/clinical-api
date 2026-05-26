"""API tests for GET /guest/cases (pre-signup history)."""

from __future__ import annotations

import uuid
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
	"file_type": "image/jpeg",
	"file_size": 16,
	"file_url": "http://testserver/media/fake-uuid.jpg",
}


async def test_list_guest_cases_returns_uploaded_case(client: AsyncClient) -> None:
	guest = await create_guest_session()

	with (
		patch(PIPELINE_TASK, MagicMock()),
		patch(STORAGE_MOCK, new_callable=AsyncMock, return_value=_FAKE_METADATA),
	):
		upload = await client.post(
			f"{API}/upload",
			files={"file": _FAKE_FILE},
			headers={GUEST_HEADER: guest.guest_session_id},
		)
	assert upload.status_code == 201
	case_id = upload.json()["data"]["case_id"]

	response = await client.get(
		f"{API}/guest/cases",
		headers={GUEST_HEADER: guest.guest_session_id},
	)

	assert response.status_code == 200
	ids = [item["id"] for item in response.json()["data"]]
	assert case_id in ids
	case_row = next(item for item in response.json()["data"] if item["id"] == case_id)
	assert case_row["title"] is None


async def test_list_guest_cases_missing_header_returns_401(client: AsyncClient) -> None:
	response = await client.get(f"{API}/guest/cases")
	assert response.status_code == 401


async def test_unverified_user_cannot_create_case(client: AsyncClient) -> None:
	"""OptionalUser treats unverified JWT as anonymous; CurrentUser blocks protected routes."""
	from app.db.session import AsyncSessionLocal
	from app.models.user import User, UserRole
	from app.services.auth.tokens import create_access_token

	user_id = uuid.uuid4()
	async with AsyncSessionLocal() as session:
		session.add(
			User(
				id=user_id,
				email=f"unverified_{uuid.uuid4().hex[:8]}@clinsights.dev",
				first_name="U",
				last_name="User",
				role=UserRole.PATIENT,
				is_active=True,
				is_email_verified=False,
			)
		)
		await session.commit()

	token, _ = create_access_token(user_id)
	headers = {"Authorization": f"Bearer {token}"}

	response = await client.post(f"{API}/cases", headers=headers)
	assert response.status_code == 401

	async with AsyncSessionLocal() as session:
		row = await session.get(User, user_id)
		if row:
			await session.delete(row)
			await session.commit()
