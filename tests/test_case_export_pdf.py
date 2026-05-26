from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

import pytest

from app.db.session import AsyncSessionLocal
from app.models.ai_interpretation import AIInterpretation, Confidence, InterpretationStatus, RiskLevel
from app.models.lab_result import LabResult, OCRStatus
from app.models.medical_case import MedicalCase, MedicalCaseStatus
from app.models.user import User, UserRole
from app.services.auth.tokens import create_access_token
from app.services.guest import create_guest_session

pytestmark = pytest.mark.asyncio(loop_scope="session")

API = "/api/v1"


async def _seed_export_case(
	*,
	user_id: uuid.UUID | None,
	guest_session_id: uuid.UUID | None,
	with_complete_interpretation: bool,
) -> uuid.UUID:
	case_id = uuid.uuid4()
	now = datetime.now(timezone.utc) - timedelta(hours=2)

	async with AsyncSessionLocal() as session:
		session.add(
			MedicalCase(
				id=case_id,
				user_id=user_id,
				guest_session_id=guest_session_id,
				status=MedicalCaseStatus.COMPLETE,
				created_at=now,
			)
		)
		session.add(
			LabResult(
				medical_case_id=case_id,
				file={"name": "panel.jpg", "url": "https://example.com/panel.jpg"},
				ocr_status=OCRStatus.COMPLETE,
				extracted_values={"tests": []},
				created_at=now,
			)
		)
		interp = AIInterpretation(
			medical_case_id=case_id,
			summary="<b>Stable</b> & improving.",
			value_breakdown=[
				{"metric": "Hemoglobin", "value": "12.4", "unit": "g/dL", "status": "normal"},
				{"metric": "WBC", "value": "11.1", "unit": "10^9/L", "status": "caution"},
			],
			suggested_questions=["What should I <ask> next?", "When should I repeat the test?"],
			risk_level=RiskLevel.LOW,
			confidence=Confidence.HIGH,
			generated_at=now,
		)
		interp.status = InterpretationStatus.COMPLETE if with_complete_interpretation else InterpretationStatus.PROCESSING
		session.add(interp)
		await session.commit()

	return case_id


async def test_case_export_returns_pdf_download(client, test_user, auth_headers):
	case_id = await _seed_export_case(
		user_id=test_user.id,
		guest_session_id=None,
		with_complete_interpretation=True,
	)

	resp = await client.get(f"{API}/cases/{case_id}/export", headers=auth_headers)

	assert resp.status_code == 200
	assert resp.headers["content-type"] == "application/pdf"
	assert resp.headers["content-disposition"] == f'attachment; filename="clinsight-report-{case_id}.pdf"'
	pdf_bytes = resp.content
	for marker in [
		b"ClinInsights",
		b"AI Lab Result Interpretation Report",
		b"Patient Information",
		b"AI Summary",
		b"Lab Results Breakdown",
		b"Suggested Questions for Your Doctor",
		b"MEDICAL DISCLAIMER",
	]:
		assert marker in pdf_bytes


async def test_case_export_without_completed_interpretation_returns_404(client, test_user, auth_headers):
	case_id = await _seed_export_case(
		user_id=test_user.id,
		guest_session_id=None,
		with_complete_interpretation=False,
	)

	resp = await client.get(f"{API}/cases/{case_id}/export", headers=auth_headers)

	assert resp.status_code == 404


async def test_case_export_wrong_user_returns_403(client, test_user):
	case_id = await _seed_export_case(
		user_id=test_user.id,
		guest_session_id=None,
		with_complete_interpretation=True,
	)

	other_user = User(
		id=uuid.uuid4(),
		email=f"export_other_{uuid.uuid4().hex[:8]}@clinsights.dev",
		first_name="Other",
		last_name="User",
		role=UserRole.PATIENT,
		is_active=True,
		is_email_verified=True,
	)
	async with AsyncSessionLocal() as session:
		session.add(other_user)
		await session.commit()

	token, _ = create_access_token(other_user.id)
	resp = await client.get(f"{API}/cases/{case_id}/export", headers={"Authorization": f"Bearer {token}"})

	assert resp.status_code == 403

	async with AsyncSessionLocal() as session:
		await session.delete(other_user)
		await session.commit()


async def test_guest_case_export_works_with_guest_header(client):
	guest = await create_guest_session()
	case_id = await _seed_export_case(
		user_id=None,
		guest_session_id=uuid.UUID(guest.guest_session_id),
		with_complete_interpretation=True,
	)

	resp = await client.get(
		f"{API}/cases/{case_id}/export",
		headers={"X-Guest-Session-Id": guest.guest_session_id},
	)

	assert resp.status_code == 200
	assert resp.headers["content-disposition"] == f'attachment; filename="clinsight-report-{case_id}.pdf"'