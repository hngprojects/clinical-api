from __future__ import annotations

import uuid

import pytest

from app.core.config import get_settings
from app.core.security import hash_password
from app.db.session import AsyncSessionLocal
from app.models.user import User, UserRole
from app.services.auth.tokens import create_access_token

pytestmark = pytest.mark.asyncio(loop_scope="session")


async def _create_user(*, email: str, password: str, verified: bool) -> User:
	user = User(
		id=uuid.uuid4(),
		email=email,
		first_name="Rate",
		last_name="Limit",
		role=UserRole.PATIENT,
		is_active=True,
		is_email_verified=verified,
		password_hash=hash_password(password),
	)
	async with AsyncSessionLocal() as session:
		session.add(user)
		await session.commit()
		await session.refresh(user)
	return user


async def _delete_user(user_id: uuid.UUID) -> None:
	async with AsyncSessionLocal() as session:
		user = await session.get(User, user_id)
		if user is not None:
			await session.delete(user)
			await session.commit()


def _auth_headers(user_id: uuid.UUID) -> dict[str, str]:
	token, _ = create_access_token(user_id)
	return {"Authorization": f"Bearer {token}"}


async def test_login_rate_limit_blocks_after_multiple_failures(client) -> None:
	settings = get_settings()
	user = await _create_user(
		email=f"login_rl_{uuid.uuid4().hex[:8]}@clinsights.dev",
		password="Password123!",
		verified=True,
	)
	try:
		for _ in range(settings.LOGIN_FAILURE_RATE_LIMIT):
			response = await client.post(
				"/api/v1/auth/login",
				json={"email": user.email, "password": "WrongPassword!", "device_id": "rl-matrix"},
			)
			assert response.status_code in (401, 404)

		blocked = await client.post(
			"/api/v1/auth/login",
			json={"email": user.email, "password": "WrongPassword!", "device_id": "rl-matrix"},
		)
		assert blocked.status_code == 429
		assert blocked.json()["message"] == "Too many failed login attempts. Please try again later."
	finally:
		await _delete_user(user.id)


async def test_forgot_password_rate_limit_blocks_after_multiple_requests(client) -> None:
	statuses: list[int] = []
	for _ in range(4):
		response = await client.post(
			"/api/v1/auth/forgot-password",
			json={"email": f"fp_{uuid.uuid4().hex[:8]}@clinsights.dev"},
		)
		statuses.append(response.status_code)
		if response.status_code == 429:
			break
	assert statuses[-1] == 429


async def test_resend_otp_rate_limit_blocks_after_multiple_requests(client) -> None:
	user = await _create_user(
		email=f"resend_{uuid.uuid4().hex[:8]}@clinsights.dev",
		password="Password123!",
		verified=False,
	)
	try:
		statuses: list[int] = []
		for _ in range(4):
			response = await client.post("/api/v1/auth/resend-otp", json={"email": user.email})
			statuses.append(response.status_code)
			if response.status_code == 429:
				break
		assert statuses[-1] == 429
	finally:
		await _delete_user(user.id)


async def test_verify_otp_rate_limit_blocks_after_multiple_failures(client) -> None:
	user = await _create_user(
		email=f"verifyotp_{uuid.uuid4().hex[:8]}@clinsights.dev",
		password="Password123!",
		verified=False,
	)
	try:
		statuses: list[int] = []
		for _ in range(6):
			response = await client.post(
				"/api/v1/auth/verify-otp",
				json={"email": user.email, "code": "000000", "device_id": "rl-matrix"},
			)
			statuses.append(response.status_code)
			if response.status_code == 429:
				break
		assert statuses[-1] == 429
	finally:
		await _delete_user(user.id)


async def test_reset_password_rate_limit_blocks_after_multiple_failures(client) -> None:
	user = await _create_user(
		email=f"resetrl_{uuid.uuid4().hex[:8]}@clinsights.dev",
		password="Password123!",
		verified=True,
	)
	try:
		statuses: list[int] = []
		for _ in range(6):
			response = await client.post(
				"/api/v1/auth/reset-password",
				json={"email": user.email, "token": "111111", "new_password": "StrongPass123!"},
			)
			statuses.append(response.status_code)
			if response.status_code == 429:
				break
		assert statuses[-1] == 429
	finally:
		await _delete_user(user.id)


async def test_reset_password_rejects_weak_new_password(client) -> None:
	user = await _create_user(
		email=f"weakpwd_{uuid.uuid4().hex[:8]}@clinsights.dev",
		password="Password123!",
		verified=True,
	)
	try:
		response = await client.post(
			"/api/v1/auth/reset-password",
			json={"email": user.email, "token": "111111", "new_password": "weakpass"},
		)
		assert response.status_code == 422
	finally:
		await _delete_user(user.id)


async def test_email_update_rate_limit_blocks_after_multiple_requests(client) -> None:
	user = await _create_user(
		email=f"emailupdate_{uuid.uuid4().hex[:8]}@clinsights.dev",
		password="Password123!",
		verified=True,
	)
	try:
		headers = _auth_headers(user.id)
		statuses: list[int] = []
		for _ in range(4):
			response = await client.post(
				"/api/v1/users/me/email",
				json={"email": f"new_{uuid.uuid4().hex[:8]}@clinsights.dev", "password": "Password123!"},
				headers=headers,
			)
			statuses.append(response.status_code)
			if response.status_code == 429:
				break
		assert statuses[-1] == 429
	finally:
		await _delete_user(user.id)


async def test_verify_email_update_rate_limit_blocks_after_multiple_failures(client) -> None:
	user = await _create_user(
		email=f"verifyemail_{uuid.uuid4().hex[:8]}@clinsights.dev",
		password="Password123!",
		verified=True,
	)
	try:
		headers = _auth_headers(user.id)
		start = await client.post(
			"/api/v1/users/me/email",
			json={"email": f"pending_{uuid.uuid4().hex[:8]}@clinsights.dev", "password": "Password123!"},
			headers=headers,
		)
		assert start.status_code == 200

		statuses: list[int] = []
		for _ in range(6):
			response = await client.post(
				"/api/v1/users/me/email/verify",
				json={"token": "000000"},
				headers=headers,
			)
			statuses.append(response.status_code)
			if response.status_code == 429:
				break
		assert statuses[-1] == 429
	finally:
		await _delete_user(user.id)
