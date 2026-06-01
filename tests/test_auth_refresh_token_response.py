"""Auth responses include refresh_token for mobile clients."""

from __future__ import annotations

import uuid

import pytest
from httpx import AsyncClient

from app.db.session import AsyncSessionLocal
from app.models.user import User, UserRole

pytestmark = pytest.mark.asyncio(loop_scope="session")

API = "/api/v1/auth"


async def _create_verified_user(*, password: str = "Password123!") -> User:
	from app.core.security import hash_password

	user = User(
		id=uuid.uuid4(),
		email=f"refresh_body_{uuid.uuid4().hex[:8]}@clinsights.dev",
		first_name="Refresh",
		last_name="Body",
		role=UserRole.PATIENT,
		is_active=True,
		is_email_verified=True,
		password_hash=hash_password(password),
	)
	async with AsyncSessionLocal() as db:
		db.add(user)
		await db.commit()
		await db.refresh(user)
	return user


async def test_login_returns_refresh_token_in_body(client: AsyncClient) -> None:
	user = await _create_verified_user()
	password = "Password123!"

	login = await client.post(
		f"{API}/login",
		json={"email": user.email, "password": password, "device_id": "mobile-1", "platform": "ios"},
	)
	assert login.status_code == 200
	data = login.json()["data"]
	assert data["access_token"]
	assert data["refresh_token"]
	assert login.cookies.get("refresh_token") == data["refresh_token"]

	async with AsyncSessionLocal() as db:
		await db.delete(user)
		await db.commit()


async def test_refresh_accepts_body_without_cookie(client: AsyncClient) -> None:
	user = await _create_verified_user()
	password = "Password123!"

	login = await client.post(
		f"{API}/login",
		json={"email": user.email, "password": password, "device_id": "mobile-2", "platform": "ios"},
	)
	assert login.status_code == 200
	refresh = login.json()["data"]["refresh_token"]

	rotated = await client.post(
		f"{API}/refresh",
		json={"refresh_token": refresh},
	)
	assert rotated.status_code == 200
	body = rotated.json()["data"]
	assert body["access_token"]
	assert body["refresh_token"]
	assert body["refresh_token"] != refresh
	assert rotated.cookies.get("refresh_token") == body["refresh_token"]

	async with AsyncSessionLocal() as db:
		await db.delete(user)
		await db.commit()
