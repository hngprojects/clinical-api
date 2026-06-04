"""Tests for GET /cases/{id}/full aggregated case detail."""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

import pytest

from app.db.session import AsyncSessionLocal
from app.models.ai_interpretation import AIInterpretation, InterpretationStatus, RiskLevel
from app.models.chat import Chat, SenderType
from app.models.lab_result import LabResult, OCRStatus
from app.models.medical_case import MedicalCase, MedicalCaseStatus
from app.models.user import User, UserRole
from app.services.auth.tokens import create_access_token
from app.services.guest import create_guest_session

pytestmark = pytest.mark.asyncio(loop_scope="session")

API = "/api/v1"

EXTRACTED = {
	"tests": [
		{"name": "Haemoglobin", "value": "11.2", "unit": "g/dL", "reference_range": "12.0–17.5"},
	]
}


async def _seed_case_minimal(user: User, *, with_interp: bool, with_chat: bool) -> uuid.UUID:
	case_id = uuid.uuid4()
	lab_id = uuid.uuid4()
	now = datetime.now(timezone.utc)

	async with AsyncSessionLocal() as session:
		session.add(
			MedicalCase(
				id=case_id,
				user_id=user.id,
				status=MedicalCaseStatus.COMPLETE,
				created_at=now,
			)
		)
		session.add(
			LabResult(
				id=lab_id,
				medical_case_id=case_id,
				file={
					"name": "panel.jpg",
					"url": "https://example.com/panel.jpg",
					"mime_type": "image/jpeg",
				},
				ocr_status=OCRStatus.COMPLETE,
				extracted_values=EXTRACTED,
				created_at=now,
			)
		)
		if with_interp:
			session.add(
				AIInterpretation(
					id=uuid.uuid4(),
					medical_case_id=case_id,
					summary="Low haemoglobin.",
					status=InterpretationStatus.COMPLETE,
					risk_level=RiskLevel.MODERATE,
					suggested_questions=["What food helps iron?", "Do I need supplements?", "Retest when?"],
					generated_at=now,
				)
			)
		if with_chat:
			session.add(
				Chat(
					id=uuid.uuid4(),
					user_id=user.id,
					medical_case_id=case_id,
					sender_type=SenderType.PATIENT,
					content={"text": "Question one"},
					sent_at=now,
				)
			)
		await session.commit()

	return case_id


async def test_full_case_returns_nested_payload(client, test_user, auth_headers):
	case_id = await _seed_case_minimal(test_user, with_interp=True, with_chat=True)

	resp = await client.get(f"{API}/cases/{case_id}/full", headers=auth_headers)
	assert resp.status_code == 200
	body = resp.json()
	assert body["status"] == "success"
	data = body["data"]
	assert data["case"]["id"] == str(case_id)
	assert len(data["lab_results"]) == 1
	assert data["lab_results"][0]["ocr_status"] == "complete"
	assert data["lab_results"][0]["file"]["mime_type"] == "image/jpeg"
	assert data["interpretation"] is not None
	assert data["interpretation"]["summary"] == "Low haemoglobin."
	assert len(data["chats"]) == 1
	assert data["chats"][0]["content"]["text"] == "Question one"


async def test_full_case_without_interpretation_returns_null(client, test_user, auth_headers):
	case_id = await _seed_case_minimal(test_user, with_interp=False, with_chat=False)

	resp = await client.get(f"{API}/cases/{case_id}/full", headers=auth_headers)
	assert resp.status_code == 200
	data = resp.json()["data"]
	assert data["interpretation"] is None
	assert len(data["lab_results"]) == 1
	assert data["chats"] == []


async def test_full_case_wrong_user_returns_404(client, test_user):
	case_id = await _seed_case_minimal(test_user, with_interp=False, with_chat=False)

	other = User(
		id=uuid.uuid4(),
		email=f"full_other_{uuid.uuid4().hex[:8]}@clinsights.dev",
		first_name="O",
		last_name="User",
		role=UserRole.PATIENT,
		is_active=True,
		is_email_verified=True,
	)
	async with AsyncSessionLocal() as session:
		session.add(other)
		await session.commit()

	token, _ = create_access_token(other.id)
	resp = await client.get(
		f"{API}/cases/{case_id}/full",
		headers={"Authorization": f"Bearer {token}"},
	)

	assert resp.status_code == 404

	async with AsyncSessionLocal() as session:
		await session.delete(other)
		await session.commit()


async def test_full_case_guest_session_allowed(client):
	guest_session = (await create_guest_session()).guest_session_id
	case_id = uuid.uuid4()
	lab_id = uuid.uuid4()
	now = datetime.now(timezone.utc)

	async with AsyncSessionLocal() as session:
		session.add(
			MedicalCase(
				id=case_id,
				user_id=None,
				guest_session_id=uuid.UUID(guest_session),
				status=MedicalCaseStatus.PENDING,
				created_at=now,
			)
		)
		session.add(
			LabResult(
				id=lab_id,
				medical_case_id=case_id,
				file={
					"name": "g.jpg",
					"url": "https://example.com/g.jpg",
					"mime_type": "image/jpeg",
				},
				ocr_status=OCRStatus.PENDING,
				created_at=now,
			)
		)
		await session.commit()

	resp = await client.get(
		f"{API}/cases/{case_id}/full",
		headers={"X-Guest-Session-Id": guest_session},
	)
	assert resp.status_code == 200
	data = resp.json()["data"]
	assert data["case"]["guest_session_id"] == guest_session
	assert data["interpretation"] is None


async def test_full_case_unknown_returns_404(client, auth_headers):
	resp = await client.get(f"{API}/cases/{uuid.uuid4()}/full", headers=auth_headers)
	assert resp.status_code == 404


async def test_full_case_chats_are_chronological(client, test_user, auth_headers):
	case_id = uuid.uuid4()
	lab_id = uuid.uuid4()
	now = datetime.now(timezone.utc)

	t_old = now - timedelta(minutes=5)
	t_new = now

	async with AsyncSessionLocal() as session:
		session.add(
			MedicalCase(
				id=case_id,
				user_id=test_user.id,
				status=MedicalCaseStatus.COMPLETE,
				created_at=now,
			)
		)
		session.add(
			LabResult(
				id=lab_id,
				medical_case_id=case_id,
				file={
					"name": "p.jpg",
					"url": "https://example.com/p.jpg",
					"mime_type": "image/jpeg",
				},
				ocr_status=OCRStatus.COMPLETE,
				extracted_values=EXTRACTED,
				created_at=now,
			)
		)
		session.add(
			Chat(
				id=uuid.uuid4(),
				user_id=test_user.id,
				medical_case_id=case_id,
				sender_type=SenderType.PATIENT,
				content={"text": "Older"},
				sent_at=t_old,
			)
		)
		session.add(
			Chat(
				id=uuid.uuid4(),
				user_id=test_user.id,
				medical_case_id=case_id,
				sender_type=SenderType.AI,
				content={"text": "Newer"},
				sent_at=t_new,
			)
		)
		await session.commit()

	resp = await client.get(f"{API}/cases/{case_id}/full", headers=auth_headers)
	assert resp.status_code == 200
	chats = resp.json()["data"]["chats"]
	assert [c["content"]["text"] for c in chats] == ["Older", "Newer"]
