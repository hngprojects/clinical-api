import uuid
from datetime import datetime, timedelta, timezone

import pytest

from app.core.security import hash_password
from app.db.session import AsyncSessionLocal
from app.models.doctor_verification import DoctorVerification, DoctorVerificationStatus
from app.models.medical_case import DoctorCaseStatus, MedicalCase, MedicalCaseStatus
from app.models.user import User, UserRole
from app.services.auth.tokens import create_access_token

pytestmark = pytest.mark.asyncio(loop_scope="session")


async def _user(role: UserRole, suffix: str) -> User:
	user = User(
		id=uuid.uuid4(),
		email=f"{role.value}_{suffix}_{uuid.uuid4().hex[:8]}@clinsights.dev",
		first_name="Ada" if role == UserRole.DOCTOR else "Patient",
		last_name="Doctor" if role == UserRole.DOCTOR else suffix,
		role=role,
		is_active=True,
		is_email_verified=True,
		password_hash=hash_password("Password123!"),
	)
	async with AsyncSessionLocal() as session:
		session.add(user)
		await session.commit()
	return user


async def _approved(doctor: User) -> None:
	now = datetime.now(timezone.utc)
	async with AsyncSessionLocal() as session:
		session.add(
			DoctorVerification(
				id=uuid.uuid4(),
				user_id=doctor.id,
				license_number="LIC-1",
				issuing_state="Lagos",
				license_expiry_date=now,
				specialty="Cardiology",
				status=DoctorVerificationStatus.APPROVED,
			)
		)
		await session.commit()


async def _case(doctor: User, patient: User, state: DoctorCaseStatus, when: datetime, title: str) -> MedicalCase:
	case = MedicalCase(
		id=uuid.uuid4(),
		user_id=patient.id,
		doctor_id=doctor.id,
		status=MedicalCaseStatus.COMPLETE,
		doctor_case_status=state,
		title=title,
		created_at=when,
		updated_at=when,
	)
	async with AsyncSessionLocal() as session:
		session.add(case)
		await session.commit()
	return case


def _headers(user: User) -> dict[str, str]:
	token, _ = create_access_token(user.id)
	return {"Authorization": f"Bearer {token}"}


async def _cleanup(*users: User) -> None:
	async with AsyncSessionLocal() as session:
		for user in users:
			existing = await session.get(User, user.id)
			if existing:
				await session.delete(existing)
		await session.commit()


async def test_doctor_previews_are_scoped_sorted_capped_and_empty(client):
	doctor_a = await _user(UserRole.DOCTOR, "a")
	doctor_b = await _user(UserRole.DOCTOR, "b")
	patient = await _user(UserRole.PATIENT, "One")
	other_patient = await _user(UserRole.PATIENT, "Two")
	await _approved(doctor_a)
	await _approved(doctor_b)
	now = datetime.now(timezone.utc)
	pending = [
		await _case(doctor_a, patient, DoctorCaseStatus.PENDING, now - timedelta(minutes=i), f"Review {i}")
		for i in range(6)
	]
	await _case(doctor_b, other_patient, DoctorCaseStatus.PENDING, now + timedelta(minutes=1), "Other")
	accepted = await _case(doctor_a, patient, DoctorCaseStatus.ACCEPTED, now + timedelta(minutes=2), "Accepted")
	try:
		response = await client.get("/api/v1/doctors/cases/requests?preview=true", headers=_headers(doctor_a))
		assert response.status_code == 200
		items = response.json()["data"]
		assert len(items) == 5
		assert [item["case_id"] for item in items] == [str(case.id) for case in pending[:5]]
		assert all(item["status"] == "pending" for item in items)
		assert "full_lab_report" not in items[0]
		full = await client.get("/api/v1/doctors/cases/requests", headers=_headers(doctor_a))
		assert full.status_code == 200
		assert len(full.json()["data"]) == 6

		response = await client.get("/api/v1/doctors/cases?status=accepted&preview=true", headers=_headers(doctor_a))
		assert response.status_code == 200
		assert [item["case_id"] for item in response.json()["data"]] == [str(accepted.id)]
		assert (await client.get("/api/v1/doctors/cases/requests?preview=true", headers=_headers(doctor_b))).json()[
			"data"
		][0]["case_id"] != str(pending[0].id)

	finally:
		await _cleanup(doctor_a, doctor_b, patient, other_patient)


async def test_acceptance_moves_case_between_previews(client):
	doctor = await _user(UserRole.DOCTOR, "transition")
	patient = await _user(UserRole.PATIENT, "transition")
	await _approved(doctor)
	case = await _case(doctor, patient, DoctorCaseStatus.PENDING, datetime.now(timezone.utc), "Blood Test")
	try:
		accept = await client.post(f"/api/v1/doctors/cases/{case.id}/accept", headers=_headers(doctor))
		assert accept.status_code == 200
		pending = await client.get("/api/v1/doctors/cases/requests?preview=true", headers=_headers(doctor))
		accepted = await client.get("/api/v1/doctors/cases?status=accepted&preview=true", headers=_headers(doctor))
		assert pending.json()["data"] == []
		assert accepted.json()["data"][0]["case_id"] == str(case.id)
		assert (
			await client.post(f"/api/v1/doctors/cases/{case.id}/accept", headers=_headers(doctor))
		).status_code == 404
	finally:
		await _cleanup(doctor, patient)


async def test_unapproved_doctor_cannot_read_previews(client):
	doctor = await _user(UserRole.DOCTOR, "unapproved")
	try:
		response = await client.get("/api/v1/doctors/cases/requests?preview=true", headers=_headers(doctor))
		assert response.status_code == 403
	finally:
		await _cleanup(doctor)


async def test_empty_doctor_previews_return_empty_success(client):
	doctor = await _user(UserRole.DOCTOR, "empty")
	await _approved(doctor)
	try:
		requests = await client.get("/api/v1/doctors/cases/requests?preview=true", headers=_headers(doctor))
		accepted = await client.get("/api/v1/doctors/cases?status=accepted&preview=true", headers=_headers(doctor))
		assert requests.status_code == accepted.status_code == 200
		assert requests.json()["data"] == []
		assert accepted.json()["data"] == []
	finally:
		await _cleanup(doctor)
