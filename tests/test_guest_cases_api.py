"""API tests for GET /guest/cases (pre-signup history)."""

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
_UPLOAD = {
	"file": {"name": "panel.jpg", "url": "https://storage.example.com/panel.jpg"},
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


async def test_list_guest_cases_returns_uploaded_case(client: AsyncClient, fake_redis: FakeRedis) -> None:
	guest = await create_guest_session(redis=fake_redis)
	mock_task = MagicMock()

	with patch(PIPELINE_TASK, mock_task):
		upload = await client.post(
			f"{API}/upload",
			json={**_UPLOAD, "guest_session_id": guest.guest_session_id},
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
