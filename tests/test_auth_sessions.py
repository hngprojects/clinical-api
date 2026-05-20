"""PR4: per-device auth_sessions and AuthSessionManager."""

from __future__ import annotations

import uuid

import pytest
from httpx import AsyncClient
from sqlalchemy import select

from app.db.session import AsyncSessionLocal
from app.models.auth_session import AuthSession
from app.models.user import User, UserRole
from app.repositories.auth_session import AuthSessionRepository
from app.services.auth_sessions import AuthSessionManager

pytestmark = pytest.mark.asyncio(loop_scope="session")

API = "/api/v1/auth"


async def _create_verified_user(*, password: str = "Password123!") -> User:
	from app.core.security import hash_password

	user = User(
		id=uuid.uuid4(),
		email=f"auth_sess_{uuid.uuid4().hex[:8]}@clinsights.dev",
		first_name="Auth",
		last_name="Session",
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


async def test_login_creates_auth_session_row(client: AsyncClient) -> None:
	email = f"login_sess_{uuid.uuid4().hex[:8]}@clinsights.dev"
	password = "Password123!"

	signup = await client.post(
		f"{API}/signup",
		json={
			"first_name": "S",
			"last_name": "User",
			"email": email,
			"password": password,
			"confirm_password": password,
		},
	)
	assert signup.status_code == 201

	async with AsyncSessionLocal() as db:
		user = (await db.execute(select(User).where(User.email == email))).scalar_one()
		user.is_email_verified = True
		await db.commit()

	login = await client.post(
		f"{API}/login",
		json={"email": email, "password": password, "device_id": "iphone-1", "platform": "ios"},
	)
	assert login.status_code == 200
	refresh = login.cookies.get("refresh_token")
	assert refresh

	async with AsyncSessionLocal() as db:
		user = (await db.execute(select(User).where(User.email == email))).scalar_one()
		sessions = (
			await db.execute(select(AuthSession).where(AuthSession.user_id == user.id, AuthSession.revoked.is_(False)))
		).scalars().all()
		assert len(sessions) == 1
		assert sessions[0].device_id == "ios:iphone-1"

	async with AsyncSessionLocal() as db:
		await db.delete(user)
		await db.commit()


async def test_same_device_login_reuses_one_active_session(client: AsyncClient) -> None:
	user = await _create_verified_user()
	password = "Password123!"

	first = await client.post(
		f"{API}/login",
		json={"email": user.email, "password": password, "device_id": "web-1"},
	)
	second = await client.post(
		f"{API}/login",
		json={"email": user.email, "password": password, "device_id": "web-1"},
	)
	assert first.status_code == 200
	assert second.status_code == 200

	async with AsyncSessionLocal() as db:
		active = (
			await db.execute(
				select(AuthSession).where(
					AuthSession.user_id == user.id,
					AuthSession.revoked.is_(False),
				)
			)
		).scalars().all()
		assert len(active) == 1

	async with AsyncSessionLocal() as db:
		await db.delete(user)
		await db.commit()


async def test_refresh_rotates_auth_session_hash() -> None:
	user = await _create_verified_user()
	async with AsyncSessionLocal() as db:
		manager = AuthSessionManager(AuthSessionRepository(db))
		issue = await manager.create(user.id, "rot-device", platform="web")
		old_refresh = issue.refresh_token
		old_hash = manager._refresh_hash(old_refresh)

		rotated = await manager.rotate_refresh(old_refresh)
		assert rotated.refresh_token != old_refresh

		row = await AuthSessionRepository(db).get_by_refresh_token(old_hash)
		assert row is None
		new_row = await AuthSessionRepository(db).get_by_refresh_token(manager._refresh_hash(rotated.refresh_token))
		assert new_row is not None

		await db.delete(user)
		await db.commit()


async def test_logout_revokes_auth_session_row(client: AsyncClient) -> None:
	user = await _create_verified_user()

	login = await client.post(
		f"{API}/login",
		json={"email": user.email, "password": "Password123!", "device_id": "logout-dev"},
	)
	assert login.status_code == 200
	access = login.json()["data"]["access_token"]
	refresh = login.cookies.get("refresh_token")
	assert refresh

	logout = await client.post(
		f"{API}/logout",
		headers={"Authorization": f"Bearer {access}"},
		cookies={"refresh_token": refresh},
	)
	assert logout.status_code == 200

	async with AsyncSessionLocal() as db:
		sessions = (
			await db.execute(select(AuthSession).where(AuthSession.user_id == user.id))
		).scalars().all()
		assert sessions
		assert all(s.revoked for s in sessions)

	async with AsyncSessionLocal() as db:
		await db.delete(user)
		await db.commit()
