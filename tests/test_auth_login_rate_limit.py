"""Login failure rate limiting (BUG-031)."""

from __future__ import annotations

import uuid

import pytest
from httpx import AsyncClient

from app.core.config import get_settings
from app.core.security import hash_password
from app.db.session import AsyncSessionLocal
from app.models.user import User, UserRole

API = "/api/v1/auth"
LOGIN = f"{API}/login"


async def _create_verified_user(*, email: str, password: str) -> User:
	user = User(
		id=uuid.uuid4(),
		email=email,
		first_name="Rate",
		last_name="Limit",
		role=UserRole.PATIENT,
		is_active=True,
		is_email_verified=True,
		password_hash=hash_password(password),
	)
	async with AsyncSessionLocal() as session:
		session.add(user)
		await session.commit()
	return user


@pytest.mark.asyncio(loop_scope="session")
async def test_login_rate_limits_after_repeated_failures(client: AsyncClient) -> None:
	settings = get_settings()
	email = f"login_rl_{uuid.uuid4().hex[:8]}@clinsights.dev"
	password = "Password123!"
	await _create_verified_user(email=email, password=password)

	for _ in range(settings.LOGIN_FAILURE_RATE_LIMIT):
		resp = await client.post(
			LOGIN,
			json={"email": email, "password": "WrongPassword!", "device_id": "web-1"},
		)
		assert resp.status_code in (401, 404)

	blocked = await client.post(
		LOGIN,
		json={"email": email, "password": "WrongPassword!", "device_id": "web-1"},
	)
	assert blocked.status_code == 429
	body = blocked.json()
	assert body["status"] == "error"
	assert body["message"] == "Too many failed login attempts. Please try again later."
	assert body["errors"] is None


@pytest.mark.asyncio(loop_scope="session")
async def test_login_success_clears_failure_counter(client: AsyncClient) -> None:
	settings = get_settings()
	email = f"login_ok_{uuid.uuid4().hex[:8]}@clinsights.dev"
	password = "Password123!"
	await _create_verified_user(email=email, password=password)

	for _ in range(settings.LOGIN_FAILURE_RATE_LIMIT):
		await client.post(
			LOGIN,
			json={"email": email, "password": "WrongPassword!", "device_id": "web-1"},
		)

	success = await client.post(
		LOGIN,
		json={"email": email, "password": password, "device_id": "web-1"},
	)
	assert success.status_code == 200

	fail = await client.post(
		LOGIN,
		json={"email": email, "password": "WrongPassword!", "device_id": "web-1"},
	)
	assert fail.status_code in (401, 404)
