from __future__ import annotations

import uuid
from datetime import datetime, timezone
import pytest

from app.core.security import hash_password
from app.db.session import AsyncSessionLocal
from app.models.user import User, UserRole
from app.models.doctor_verification import DoctorVerification, DoctorVerificationStatus
from app.services.auth.tokens import create_access_token

pytestmark = pytest.mark.asyncio(loop_scope="session")


async def _create_user(*, email: str, role: UserRole = UserRole.PATIENT, verified: bool = True) -> User:
	user = User(
		id=uuid.uuid4(),
		email=email,
		first_name="Jane",
		last_name="Doe",
		role=role,
		is_active=True,
		is_email_verified=verified,
		password_hash=hash_password("Password123!"),
	)
	async with AsyncSessionLocal() as session:
		session.add(user)
		await session.commit()
		await session.refresh(user)
	return user


async def _create_verification(user_id: uuid.UUID, status: DoctorVerificationStatus, specialty: str = "Cardiology") -> DoctorVerification:
	now = datetime.now(timezone.utc)
	verification = DoctorVerification(
		id=uuid.uuid4(),
		user_id=user_id,
		license_number="LIC-12345",
		issuing_state="CA",
		license_expiry_date=now,
		specialty=specialty,
		status=status,
		rejection_reason="Incomplete documents" if status == DoctorVerificationStatus.REJECTED else None,
	)
	async with AsyncSessionLocal() as session:
		session.add(verification)
		await session.commit()
		await session.refresh(verification)
	return verification


async def _delete_user(user_id: uuid.UUID) -> None:
	async with AsyncSessionLocal() as session:
		user = await session.get(User, user_id)
		if user is not None:
			await session.delete(user)
			await session.commit()


def _auth_headers(user_id: uuid.UUID) -> dict[str, str]:
	token, _ = create_access_token(user_id)
	return {"Authorization": f"Bearer {token}"}


async def test_users_me_unauthenticated_returns_401(client) -> None:
	response = await client.get("/api/v1/users/me")
	assert response.status_code == 401


async def test_users_me_authenticated_without_verification(client) -> None:
	user = await _create_user(email=f"noverif_{uuid.uuid4().hex[:8]}@clinsights.dev")
	try:
		response = await client.get("/api/v1/users/me", headers=_auth_headers(user.id))
		assert response.status_code == 200
		data = response.json()["data"]
		assert data["email"] == user.email
		assert data["verification_status"] == "not_submitted"
		assert data["specialty"] is None
		assert data["rejection_reason"] is None
		assert "dashboard" in data
	finally:
		await _delete_user(user.id)


async def test_users_me_authenticated_with_verification(client) -> None:
	user = await _create_user(email=f"withverif_{uuid.uuid4().hex[:8]}@clinsights.dev")
	await _create_verification(user.id, DoctorVerificationStatus.APPROVED, specialty="Neurology")
	try:
		response = await client.get("/api/v1/users/me", headers=_auth_headers(user.id))
		assert response.status_code == 200
		data = response.json()["data"]
		assert data["verification_status"] == "approved"
		assert data["specialty"] == "Neurology"
	finally:
		await _delete_user(user.id)


async def test_doctors_dashboard_gate_enforcement(client) -> None:
	# 1. Unverified doctor -> 403
	user_unverified = await _create_user(
		email=f"unver_{uuid.uuid4().hex[:8]}@clinsights.dev",
		role=UserRole.DOCTOR,
		verified=False,
	)
	# 2. Pending doctor -> 403
	user_pending = await _create_user(
		email=f"pending_{uuid.uuid4().hex[:8]}@clinsights.dev",
		role=UserRole.DOCTOR,
		verified=True,
	)
	await _create_verification(user_pending.id, DoctorVerificationStatus.PENDING)
	# 3. Approved doctor -> 200
	user_approved = await _create_user(
		email=f"appr_{uuid.uuid4().hex[:8]}@clinsights.dev",
		role=UserRole.DOCTOR,
		verified=True,
	)
	await _create_verification(user_approved.id, DoctorVerificationStatus.APPROVED)

	try:
		res1 = await client.get("/api/v1/doctors/dashboard", headers=_auth_headers(user_unverified.id))
		assert res1.status_code == 401

		res2 = await client.get("/api/v1/doctors/dashboard", headers=_auth_headers(user_pending.id))
		assert res2.status_code == 403

		res3 = await client.get("/api/v1/doctors/dashboard", headers=_auth_headers(user_approved.id))
		assert res3.status_code == 200
		assert res3.json()["data"]["verification_status"] == "approved"
		assert "dashboard" in res3.json()["data"]
	finally:
		await _delete_user(user_unverified.id)
		await _delete_user(user_pending.id)
		await _delete_user(user_approved.id)


async def test_doctor_dashboard_statistics_endpoint(client) -> None:
	user = await _create_user(
		email=f"stats_{uuid.uuid4().hex[:8]}@clinsights.dev",
		role=UserRole.DOCTOR,
		verified=True,
	)
	await _create_verification(user.id, DoctorVerificationStatus.APPROVED)
	try:
		res = await client.get("/api/v1/doctors/dashboard/statistics", headers=_auth_headers(user.id))
		assert res.status_code == 200
		data = res.json()["data"]
		assert "pending_reviews" in data
		assert "accepted_cases" in data
		assert "completed_cases" in data
		assert "earnings" in data
	finally:
		await _delete_user(user.id)


async def test_doctor_duty_status_and_manual_off_duty_forbidden(client) -> None:
	user = await _create_user(
		email=f"duty_{uuid.uuid4().hex[:8]}@clinsights.dev",
		role=UserRole.DOCTOR,
		verified=True,
	)
	await _create_verification(user.id, DoctorVerificationStatus.APPROVED)
	try:
		# 1. Switch ON DUTY -> 200 OK
		res_on = await client.post(
			"/api/v1/doctors/duty-status",
			json={"is_on_duty": True},
			headers=_auth_headers(user.id),
		)
		assert res_on.status_code == 200
		data_on = res_on.json()["data"]
		assert data_on["is_on_duty"] is True
		assert data_on["remaining_duty_seconds"] > 0

		# 2. Attempt manual OFF DUTY -> 403 Forbidden
		res_off = await client.post(
			"/api/v1/doctors/duty-status",
			json={"is_on_duty": False},
			headers=_auth_headers(user.id),
		)
		assert res_off.status_code == 403
	finally:
		await _delete_user(user.id)


async def test_doctor_dismiss_verification_banner(client) -> None:
	user = await _create_user(
		email=f"dismiss_{uuid.uuid4().hex[:8]}@clinsights.dev",
		role=UserRole.DOCTOR,
		verified=True,
	)
	await _create_verification(user.id, DoctorVerificationStatus.PENDING)
	try:
		# Dismiss banner
		res_dismiss = await client.post(
			"/api/v1/doctors/verification/dismiss",
			headers=_auth_headers(user.id),
		)
		assert res_dismiss.status_code == 200

		# Verify /me payload shows banner is dismissed
		res_me = await client.get("/api/v1/users/me", headers=_auth_headers(user.id))
		assert res_me.status_code == 200
		data_me = res_me.json()["data"]
		assert data_me["is_verification_dismissed"] is True
		assert data_me["show_verification_banner"] is False
	finally:
		await _delete_user(user.id)

