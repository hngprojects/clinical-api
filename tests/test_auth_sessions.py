"""PR4: per-device auth_sessions and AuthSessionManager."""

from __future__ import annotations

import uuid
from unittest.mock import patch

import pytest
from httpx import AsyncClient
from sqlalchemy import select

from app.core.security import verify_password
from app.db.session import AsyncSessionLocal
from app.models.otp import OtpCode, OtpPurpose
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


async def _latest_reset_otp(user_id: uuid.UUID) -> OtpCode | None:
	async with AsyncSessionLocal() as db:
		return (
			await db.execute(
				select(OtpCode)
				.where(OtpCode.user_id == user_id, OtpCode.purpose == OtpPurpose.RESET_PASSWORD)
				.order_by(OtpCode.created_at.desc())
			)
		).scalar_one_or_none()


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
	assert login.json()["data"]["refresh_token"] == refresh

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


async def test_forgot_password_sends_six_digit_otp(client: AsyncClient) -> None:
	user = await _create_verified_user()

	with patch("app.api.v1.endpoints.auth.send_otp_email_task.delay") as mock_delay:
		response = await client.post("/api/v1/auth/forgot-password", json={"email": user.email})

	assert response.status_code == 200
	assert mock_delay.called
	assert mock_delay.call_args.kwargs["to_email"] == user.email
	assert mock_delay.call_args.kwargs["purpose"] == OtpPurpose.RESET_PASSWORD.value
	code = mock_delay.call_args.kwargs["code"]
	assert len(code) == 6
	assert code.isdigit()

	otp_row = await _latest_reset_otp(user.id)
	assert otp_row is not None
	assert otp_row.purpose == OtpPurpose.RESET_PASSWORD

	async with AsyncSessionLocal() as db:
		await db.delete(user)
		await db.commit()


async def test_reset_password_accepts_otp_and_changes_password(client: AsyncClient) -> None:
	user = await _create_verified_user()
	new_password = "NewPassword123!"

	with patch("app.api.v1.endpoints.auth.send_otp_email_task.delay") as mock_delay:
		forgot = await client.post("/api/v1/auth/forgot-password", json={"email": user.email})
	assert forgot.status_code == 200
	code = mock_delay.call_args.kwargs["code"]

	reset = await client.post(
		"/api/v1/auth/reset-password",
		json={"email": user.email, "token": code, "new_password": new_password},
	)
	assert reset.status_code == 200
	assert reset.json()["message"] == "Password reset successfully."

	async with AsyncSessionLocal() as db:
		updated = await db.get(User, user.id)
		assert updated is not None
		assert verify_password(new_password, updated.password_hash)

	login = await client.post(
		"/api/v1/auth/login",
		json={"email": user.email, "password": new_password, "device_id": "reset-device"},
	)
	assert login.status_code == 200

	async with AsyncSessionLocal() as db:
		await db.delete(updated)
		await db.commit()
