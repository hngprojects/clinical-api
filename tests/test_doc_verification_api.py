from __future__ import annotations

import io
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
		first_name="Doctor",
		last_name="VerificationTest",
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


async def _delete_user(user_id: uuid.UUID) -> None:
	async with AsyncSessionLocal() as session:
		user = await session.get(User, user_id)
		if user is not None:
			await session.delete(user)
			await session.commit()


def _auth_headers(user_id: uuid.UUID) -> dict[str, str]:
	token, _ = create_access_token(user_id)
	return {"Authorization": f"Bearer {token}"}


async def test_doctor_verification_workflow(client) -> None:
	doctor = await _create_user(email=f"doc_ver_{uuid.uuid4().hex[:8]}@clinsights.dev", role=UserRole.DOCTOR)
	admin = await _create_user(email=f"admin_ver_{uuid.uuid4().hex[:8]}@clinsights.dev", role=UserRole.ADMIN)

	try:
		# 1. Check status initially (should return not_submitted)
		headers = _auth_headers(doctor.id)
		res_status = await client.get("/api/v1/doctors/verification/status", headers=headers)
		assert res_status.status_code == 200
		assert res_status.json()["data"]["status"] == "not_submitted"

		# 2. Submit verification request
		data = {
			"license_number": "ML-99887",
			"issuing_state": "NY",
			"license_expiry_date": "2030-12-31",
			"specialty": "Pediatrics",
		}
		files = {
			"medical_license": ("license.pdf", b"mock medical license content", "application/pdf"),
			"government_id": ("id.png", b"mock government id content", "image/png"),
		}
		res_submit = await client.post(
			"/api/v1/doctors/verification",
			data=data,
			files=files,
			headers=headers,
		)
		assert res_submit.status_code == 201
		verif_data = res_submit.json()["data"]
		assert verif_data["status"] == "pending"
		assert verif_data["license_number"] == "ML-99887"
		assert len(verif_data["documents"]) == 2

		verification_id = verif_data["id"]

		# 3. Block concurrent duplicate submissions
		res_duplicate = await client.post(
			"/api/v1/doctors/verification",
			data=data,
			files=files,
			headers=headers,
		)
		assert res_duplicate.status_code == 409

		# 4. Reject invalid file type
		bad_files = {
			"medical_license": ("license.exe", b"malicious code", "application/x-msdownload"),
			"government_id": ("id.png", b"mock government id content", "image/png"),
		}
		doctor2 = await _create_user(
			email=f"doc_badfile_{uuid.uuid4().hex[:8]}@clinsights.dev",
			role=UserRole.DOCTOR,
		)
		res_badfile = await client.post(
			"/api/v1/doctors/verification",
			data=data,
			files=bad_files,
			headers=_auth_headers(doctor2.id),
		)
		assert res_badfile.status_code == 400
		await _delete_user(doctor2.id)

		# 5. Reject file size over 10MB
		huge_data = b"0" * (10 * 1024 * 1024 + 1)
		huge_files = {
			"medical_license": ("large_license.pdf", huge_data, "application/pdf"),
			"government_id": ("id.png", b"mock government id content", "image/png"),
		}
		doctor3 = await _create_user(
			email=f"doc_huge_{uuid.uuid4().hex[:8]}@clinsights.dev",
			role=UserRole.DOCTOR,
		)
		res_huge = await client.post(
			"/api/v1/doctors/verification",
			data=data,
			files=huge_files,
			headers=_auth_headers(doctor3.id),
		)
		assert res_huge.status_code == 400
		assert "exceeds the maximum size limit of 10MB" in res_huge.json()["message"]
		await _delete_user(doctor3.id)

		# 6. Admin updates status (first reject)
		admin_headers = _auth_headers(admin.id)
		res_reject = await client.patch(
			"/api/v1/doctors/verification/status",
			data={
				"verification_id": verification_id,
				"status_update": "rejected",
				"rejection_reason": "License signature is not visible.",
			},
			headers=admin_headers,
		)
		assert res_reject.status_code == 200

		# Check status is rejected
		res_status2 = await client.get("/api/v1/doctors/verification/status", headers=headers)
		assert res_status2.json()["data"]["status"] == "rejected"
		assert res_status2.json()["data"]["rejection_reason"] == "License signature is not visible."

		# 7. Resubmit rejected verification (PUT)
		res_resubmit = await client.put(
			"/api/v1/doctors/verification",
			data={
				"license_number": "ML-99887",
				"issuing_state": "NY",
				"license_expiry_date": "2030-12-31",
				"specialty": "Pediatrics",
			},
			files={
				"medical_license": ("license_fixed.pdf", b"fixed license content", "application/pdf"),
				"government_id": ("id_fixed.png", b"fixed id content", "image/png"),
			},
			headers=headers,
		)
		assert res_resubmit.status_code == 200
		assert res_resubmit.json()["data"]["status"] == "pending"
		assert res_resubmit.json()["data"]["rejection_reason"] is None

		# 8. Admin approves verification
		res_approve = await client.patch(
			"/api/v1/doctors/verification/status",
			data={
				"verification_id": verification_id,
				"status_update": "approved",
			},
			headers=admin_headers,
		)
		assert res_approve.status_code == 200

		# 9. Verify role is promoted to DOCTOR
		res_me = await client.get("/api/v1/users/me", headers=headers)
		assert res_me.json()["data"]["role"] == "doctor"
		assert res_me.json()["data"]["verification_status"] == "approved"

		# 10. Generate document signed URL
		doc_id = res_resubmit.json()["data"]["documents"][0]["id"]
		res_url = await client.get(
			f"/api/v1/doctors/verification/documents/{doc_id}",
			headers=headers,
		)
		assert res_url.status_code == 200
		signed_url = res_url.json()["data"]["signed_url"]
		assert "/view?token=" in signed_url

	finally:
		await _delete_user(doctor.id)
		await _delete_user(admin.id)
