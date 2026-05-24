"""Doctor profile API tests for profile, credentials, and verification flows."""

from __future__ import annotations

import io
import uuid
from unittest.mock import patch

import pytest
from sqlalchemy import select

from app.core.security import hash_password
from app.db.session import AsyncSessionLocal
from app.models.doctor_profile import DoctorProfile, DoctorVerificationStatus
from app.models.user import User, UserRole
from app.services.auth.tokens import create_access_token

pytestmark = pytest.mark.asyncio(loop_scope="session")

API = "/api/v1"


# ── Helpers ───────────────────────────────────────────────────────────────────

def _unique_email() -> str:
	return f"dr_{uuid.uuid4().hex[:8]}@clinsights.dev"


def _doctor_headers(user_id: uuid.UUID) -> dict[str, str]:
	token, _ = create_access_token(user_id)
	return {"Authorization": f"Bearer {token}"}


async def _create_doctor(*, email: str | None = None, verified: bool = True) -> User:
	async with AsyncSessionLocal() as session:
		user = User(
			id=uuid.uuid4(),
			email=email or _unique_email(),
			first_name="Emeka",
			last_name="Okafor",
			phone_number="08012345678",
			role=UserRole.DOCTOR,
			is_active=True,
			is_email_verified=verified,
			password_hash=hash_password("SecurePass123!"),
		)
		session.add(user)
		await session.commit()
		await session.refresh(user)
	return user


async def _create_patient(*, email: str | None = None) -> User:
	async with AsyncSessionLocal() as session:
		user = User(
			id=uuid.uuid4(),
			email=email or _unique_email(),
			first_name="Pat",
			last_name="Ient",
			role=UserRole.PATIENT,
			is_active=True,
			is_email_verified=True,
			password_hash=hash_password("SecurePass123!"),
		)
		session.add(user)
		await session.commit()
		await session.refresh(user)
	return user


async def _delete_user(user_id: uuid.UUID) -> None:
	async with AsyncSessionLocal() as session:
		user = await session.get(User, user_id)
		if user:
			await session.delete(user)
			await session.commit()


async def _get_profile(user_id: uuid.UUID) -> DoctorProfile | None:
	async with AsyncSessionLocal() as session:
		result = await session.execute(
			select(DoctorProfile).where(DoctorProfile.user_id == user_id)
		)
		return result.scalars().first()


# ── GET /doctors/me/profile ───────────────────────────────────────────────────

async def test_get_profile_creates_blank_profile(client):
	doctor = await _create_doctor()
	headers = _doctor_headers(doctor.id)

	response = await client.get(f"{API}/doctors/me/profile", headers=headers)

	assert response.status_code == 200
	data = response.json()["data"]
	assert data["user_id"] == str(doctor.id)
	assert data["verification_status"] == "incomplete"
	assert data["specialization"] is None

	await _delete_user(doctor.id)


async def test_get_profile_blocked_for_patient(client):
	patient = await _create_patient()
	headers = _doctor_headers(patient.id)

	response = await client.get(f"{API}/doctors/me/profile", headers=headers)

	assert response.status_code == 403
	await _delete_user(patient.id)


async def test_get_profile_requires_auth(client):
	response = await client.get(f"{API}/doctors/me/profile")
	assert response.status_code == 401


# ── POST /doctors/me/profile ──────────────────────────────────────────────────

async def test_save_professional_info_success(client):
	doctor = await _create_doctor()
	headers = _doctor_headers(doctor.id)

	payload = {
		"specialization": "Cardiology",
		"years_of_experience": 5,
		"hospital": "Lagos General Hospital",
	}
	response = await client.post(f"{API}/doctors/me/profile", json=payload, headers=headers)

	assert response.status_code == 200
	data = response.json()["data"]
	assert data["specialization"] == "Cardiology"
	assert data["years_of_experience"] == 5
	assert data["hospital"] == "Lagos General Hospital"

	await _delete_user(doctor.id)


async def test_save_professional_info_hospital_optional(client):
	doctor = await _create_doctor()
	headers = _doctor_headers(doctor.id)

	payload = {"specialization": "Neurology", "years_of_experience": 3}
	response = await client.post(f"{API}/doctors/me/profile", json=payload, headers=headers)

	assert response.status_code == 200
	assert response.json()["data"]["hospital"] is None

	await _delete_user(doctor.id)


async def test_save_professional_info_missing_specialization(client):
	doctor = await _create_doctor()
	headers = _doctor_headers(doctor.id)

	payload = {"years_of_experience": 3}
	response = await client.post(f"{API}/doctors/me/profile", json=payload, headers=headers)

	assert response.status_code == 422
	await _delete_user(doctor.id)


async def test_save_professional_info_blocked_for_patient(client):
	patient = await _create_patient()
	headers = _doctor_headers(patient.id)

	payload = {"specialization": "Cardiology", "years_of_experience": 5}
	response = await client.post(f"{API}/doctors/me/profile", json=payload, headers=headers)

	assert response.status_code == 403
	await _delete_user(patient.id)


# ── POST /doctors/me/profile/passport ────────────────────────────────────────

async def test_upload_passport_success(client):
	doctor = await _create_doctor()
	headers = _doctor_headers(doctor.id)

	fake_image = io.BytesIO(b"fake-image-bytes")

	with patch("app.services.doctor_profile.upload_medical_file") as mock_upload:
		mock_upload.return_value = {"file_url": "http://test/media/passport.jpg"}
		response = await client.post(
			f"{API}/doctors/me/profile/passport",
			headers=headers,
			files={"file": ("passport.jpg", fake_image, "image/jpeg")},
		)

	assert response.status_code == 200
	assert response.json()["data"]["passport_photo_url"] == "http://test/media/passport.jpg"

	await _delete_user(doctor.id)


async def test_upload_passport_invalid_file_type(client):
	doctor = await _create_doctor()
	headers = _doctor_headers(doctor.id)

	fake_pdf = io.BytesIO(b"%PDF-fake")

	with patch("app.services.doctor_profile.upload_medical_file") as mock_upload:
		response = await client.post(
			f"{API}/doctors/me/profile/passport",
			headers=headers,
			files={"file": ("doc.pdf", fake_pdf, "application/pdf")},
		)

	assert response.status_code == 400
	mock_upload.assert_not_called()
	await _delete_user(doctor.id)


# ── POST /doctors/me/credentials ─────────────────────────────────────────────

async def test_save_credentials_success(client):
	doctor = await _create_doctor()
	headers = _doctor_headers(doctor.id)

	payload = {
		"mdcn_license_number": "MDCN-12345",
		"nin": "12345678901",
	}
	response = await client.post(
		f"{API}/doctors/me/credentials", json=payload, headers=headers
	)

	assert response.status_code == 200
	data = response.json()["data"]
	assert data["mdcn_license_number"] == "******2345"
	assert data["nin"] == "*******8901"

	await _delete_user(doctor.id)


async def test_save_credentials_invalid_nin_length(client):
	doctor = await _create_doctor()
	headers = _doctor_headers(doctor.id)

	payload = {"mdcn_license_number": "MDCN-12345", "nin": "123"}  # too short
	response = await client.post(
		f"{API}/doctors/me/credentials", json=payload, headers=headers
	)

	assert response.status_code == 422
	await _delete_user(doctor.id)


async def test_save_credentials_missing_mdcn(client):
	doctor = await _create_doctor()
	headers = _doctor_headers(doctor.id)

	payload = {"nin": "12345678901"}
	response = await client.post(
		f"{API}/doctors/me/credentials", json=payload, headers=headers
	)

	assert response.status_code == 422
	await _delete_user(doctor.id)


# ── POST /doctors/me/credentials/license ─────────────────────────────────────

async def test_upload_license_success(client):
	doctor = await _create_doctor()
	headers = _doctor_headers(doctor.id)

	fake_pdf = io.BytesIO(b"%PDF-fake-license")

	with patch("app.services.doctor_profile.upload_medical_file") as mock_upload:
		mock_upload.return_value = {"file_url": "http://test/media/license.pdf"}
		response = await client.post(
			f"{API}/doctors/me/credentials/license",
			headers=headers,
			files={"file": ("license.pdf", fake_pdf, "application/pdf")},
		)

	assert response.status_code == 200
	assert response.json()["data"]["medical_license_url"] == "http://test/media/license.pdf"

	await _delete_user(doctor.id)


async def test_upload_license_invalid_type(client):
	doctor = await _create_doctor()
	headers = _doctor_headers(doctor.id)

	fake_file = io.BytesIO(b"fake-data")

	with patch("app.services.doctor_profile.upload_medical_file") as mock_upload:
		response = await client.post(
			f"{API}/doctors/me/credentials/license",
			headers=headers,
			files={"file": ("file.txt", fake_file, "text/plain")},
		)

	assert response.status_code == 400
	mock_upload.assert_not_called()
	await _delete_user(doctor.id)


# ── POST /doctors/me/credentials/submit ──────────────────────────────────────

async def test_submit_for_verification_success(client):
	doctor = await _create_doctor()
	headers = _doctor_headers(doctor.id)

	# Seed a complete profile directly in DB
	async with AsyncSessionLocal() as session:
		profile = DoctorProfile(
			user_id=doctor.id,
			specialization="Cardiology",
			years_of_experience=5,
			passport_photo_url="http://test/media/passport.jpg",
			mdcn_license_number="MDCN-12345",
			nin="12345678901",
			medical_license_url="http://test/media/license.pdf",
			verification_status=DoctorVerificationStatus.INCOMPLETE,
		)
		session.add(profile)
		await session.commit()

	response = await client.post(
		f"{API}/doctors/me/credentials/submit", headers=headers
	)

	assert response.status_code == 200
	data = response.json()["data"]
	assert data["verification_status"] == "pending_review"
	assert data["submitted_at"] is not None

	await _delete_user(doctor.id)


async def test_submit_fails_with_missing_fields(client):
	doctor = await _create_doctor()
	headers = _doctor_headers(doctor.id)

	# Profile exists but incomplete — missing passport and license
	async with AsyncSessionLocal() as session:
		profile = DoctorProfile(
			user_id=doctor.id,
			specialization="Cardiology",
			years_of_experience=5,
			verification_status=DoctorVerificationStatus.INCOMPLETE,
		)
		session.add(profile)
		await session.commit()

	response = await client.post(
		f"{API}/doctors/me/credentials/submit", headers=headers
	)

	assert response.status_code == 400
	assert "Missing required fields" in response.json()["message"]

	await _delete_user(doctor.id)


async def test_submit_fails_with_no_profile(client):
	doctor = await _create_doctor()
	headers = _doctor_headers(doctor.id)

	response = await client.post(
		f"{API}/doctors/me/credentials/submit", headers=headers
	)

	assert response.status_code == 400
	await _delete_user(doctor.id)


async def test_submit_already_verified(client):
	doctor = await _create_doctor()
	headers = _doctor_headers(doctor.id)

	async with AsyncSessionLocal() as session:
		profile = DoctorProfile(
			user_id=doctor.id,
			specialization="Cardiology",
			years_of_experience=5,
			passport_photo_url="http://test/media/passport.jpg",
			mdcn_license_number="MDCN-12345",
			nin="12345678901",
			medical_license_url="http://test/media/license.pdf",
			verification_status=DoctorVerificationStatus.VERIFIED,
		)
		session.add(profile)
		await session.commit()

	response = await client.post(
		f"{API}/doctors/me/credentials/submit", headers=headers
	)

	assert response.status_code == 400
	await _delete_user(doctor.id)