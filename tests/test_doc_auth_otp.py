from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone
import pytest

from app.core.config import get_settings
from app.core.security import hash_password
from app.db.session import AsyncSessionLocal
from app.models.user import User, UserRole
from app.models.otp import OtpCode, OtpPurpose
from app.services.auth.otp import _hash_code

pytestmark = pytest.mark.asyncio(loop_scope="session")


async def _create_unverified_user(email: str) -> User:
	user = User(
		id=uuid.uuid4(),
		email=email,
		first_name="Doctor",
		last_name="Who",
		role=UserRole.PATIENT,
		is_active=True,
		is_email_verified=False,
		password_hash=hash_password("Password123!"),
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


async def _create_otp(user_id: uuid.UUID, code: str, purpose: OtpPurpose, expired: bool = False, attempts: int = 0) -> OtpCode:
	now = datetime.now(timezone.utc)
	expires_at = now - timedelta(minutes=1) if expired else now + timedelta(minutes=10)
	otp = OtpCode(
		id=uuid.uuid4(),
		user_id=user_id,
		code_hash=_hash_code(code),
		purpose=purpose,
		attempts=attempts,
		expires_at=expires_at,
		consumed_at=None,
	)
	async with AsyncSessionLocal() as session:
		session.add(otp)
		await session.commit()
		await session.refresh(otp)
	return otp


async def test_incorrect_otp_returns_400(client) -> None:
	email = f"doc_err_{uuid.uuid4().hex[:8]}@clinsights.dev"
	user = await _create_unverified_user(email)
	await _create_otp(user.id, "123456", OtpPurpose.EMAIL_VERIFICATION)
	try:
		response = await client.post(
			"/api/v1/auth/verify-otp",
			json={"email": email, "code": "654321", "device_id": "test-device"},
		)
		assert response.status_code == 400
		assert response.json()["message"] == "The code you entered is incorrect."
	finally:
		await _delete_user(user.id)


async def test_expired_otp_returns_400(client) -> None:
	email = f"doc_exp_{uuid.uuid4().hex[:8]}@clinsights.dev"
	user = await _create_unverified_user(email)
	await _create_otp(user.id, "123456", OtpPurpose.EMAIL_VERIFICATION, expired=True)
	try:
		response = await client.post(
			"/api/v1/auth/verify-otp",
			json={"email": email, "code": "123456", "device_id": "test-device"},
		)
		assert response.status_code == 400
		assert response.json()["message"] == "This code has expired. Please request a new one."
	finally:
		await _delete_user(user.id)


async def test_otp_lockout_after_max_failures(client) -> None:
	email = f"doc_lock_{uuid.uuid4().hex[:8]}@clinsights.dev"
	user = await _create_unverified_user(email)
	await _create_otp(user.id, "123456", OtpPurpose.EMAIL_VERIFICATION)
	try:
		# Exceed failure limit
		settings = get_settings()
		for _ in range(settings.OTP_FAILURE_RATE_LIMIT):
			response = await client.post(
				"/api/v1/auth/verify-otp",
				json={"email": email, "code": "654321", "device_id": "test-device"},
			)
			assert response.status_code == 400

		# Next attempt should be locked out with 429
		response = await client.post(
			"/api/v1/auth/verify-otp",
			json={"email": email, "code": "123456", "device_id": "test-device"},
		)
		assert response.status_code == 429
		assert "Too many OTP verification attempts. Try again later." in response.json()["message"]
	finally:
		await _delete_user(user.id)


async def test_resend_otp_prevents_account_enumeration(client) -> None:
	# 1. Non-existent mixed-case email
	non_existent_email = f"NonExistent_{uuid.uuid4().hex[:8]}@CLINSIGHTS.dev"
	response1 = await client.post(
		"/api/v1/auth/resend-otp",
		json={"email": non_existent_email},
	)
	assert response1.status_code == 200
	assert response1.json()["message"] == "A new code has been sent to your email."
	assert response1.json()["data"]["email"] == non_existent_email.strip().lower()

	# 2. Existing user with mixed-case email resend
	user = await _create_user(
		email=f"registered_{uuid.uuid4().hex[:8]}@clinsights.dev",
		password="Password123!",
		verified=False,
	)
	try:
		response2 = await client.post(
			"/api/v1/auth/resend-otp",
			json={"email": user.email.upper()},
		)
		assert response2.status_code == 200
		assert response2.json()["data"]["email"] == user.email.strip().lower()
	finally:
		await _delete_user(user.id)

