"""
Tests for DELETE /cases/{id} — ownership and cascading deletes.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

import pytest
from sqlalchemy import func, select

from app.db.session import AsyncSessionLocal
from app.models.ai_interpretation import AIInterpretation, InterpretationStatus, RiskLevel
from app.models.chat import Chat, SenderType
from app.models.lab_result import LabResult, OCRStatus
from app.models.medical_case import MedicalCase, MedicalCaseStatus
from app.models.notification import Notification, NotificationType
from app.models.user import User, UserRole
from app.services.auth.tokens import create_access_token

pytestmark = pytest.mark.asyncio(loop_scope="session")

API = "/api/v1"


async def _count_related(case_id: uuid.UUID) -> tuple[int, int, int, int]:
	async with AsyncSessionLocal() as session:
		lab = await session.scalar(
			select(func.count()).select_from(LabResult).where(LabResult.medical_case_id == case_id)
		)
		interp = await session.scalar(
			select(func.count()).select_from(AIInterpretation).where(AIInterpretation.medical_case_id == case_id)
		)
		chats = await session.scalar(
			select(func.count()).select_from(Chat).where(Chat.medical_case_id == case_id)
		)
		notifs = await session.scalar(
			select(func.count()).select_from(Notification).where(Notification.medical_case_id == case_id)
		)
	return int(lab or 0), int(interp or 0), int(chats or 0), int(notifs or 0)


async def _seed_case_with_children(user: User) -> uuid.UUID:
	case_id = uuid.uuid4()
	lab_id = uuid.uuid4()
	interp_id = uuid.uuid4()
	chat_id = uuid.uuid4()
	notif_id = uuid.uuid4()
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
				file={"name": "x.jpg", "url": "https://example.com/x.jpg"},
				ocr_status=OCRStatus.COMPLETE,
				extracted_values={"tests": []},
				created_at=now,
			)
		)
		session.add(
			AIInterpretation(
				id=interp_id,
				medical_case_id=case_id,
				summary="Summary.",
				status=InterpretationStatus.COMPLETE,
				risk_level=RiskLevel.LOW,
				generated_at=now,
			)
		)
		session.add(
			Chat(
				id=chat_id,
				user_id=user.id,
				medical_case_id=case_id,
				sender_type=SenderType.PATIENT,
				content={"text": "Hi"},
				sent_at=now,
			)
		)
		session.add(
			Notification(
				id=notif_id,
				user_id=user.id,
				medical_case_id=case_id,
				type=NotificationType.INTERPRETATION_READY,
				title="Ready",
				is_read=False,
				created_at=now,
			)
		)
		await session.commit()

	return case_id


async def test_owner_deletes_case_returns_204(client, test_user, auth_headers):
	case_id = await _seed_case_with_children(test_user)

	assert await _count_related(case_id) == (1, 1, 1, 1)

	response = await client.delete(f"{API}/cases/{case_id}", headers=auth_headers)

	assert response.status_code == 204
	assert response.content == b""

	async with AsyncSessionLocal() as session:
		assert await session.get(MedicalCase, case_id) is None

	assert await _count_related(case_id) == (0, 0, 0, 0)


async def test_other_user_cannot_delete_returns_403(client, test_user, auth_headers):
	case_id = await _seed_case_with_children(test_user)

	other_user = User(
		id=uuid.uuid4(),
		email=f"other_del_{uuid.uuid4().hex[:8]}@clinsights.dev",
		first_name="Other",
		last_name="User",
		role=UserRole.PATIENT,
		is_active=True,
		is_email_verified=True,
	)
	async with AsyncSessionLocal() as session:
		session.add(other_user)
		await session.commit()

	other_token, _ = create_access_token(other_user.id)
	other_headers = {"Authorization": f"Bearer {other_token}"}

	response = await client.delete(f"{API}/cases/{case_id}", headers=other_headers)

	assert response.status_code == 403

	async with AsyncSessionLocal() as session:
		assert await session.get(MedicalCase, case_id) is not None
	
	# Verify that the case and its children still exist
	assert await _count_related(case_id) == (1, 1, 1, 1)

	async with AsyncSessionLocal() as session:
		await session.delete(other_user)
		await session.commit()
	
	# Verify that the case and its children are deleted
	assert await _count_related(case_id) == (0, 0, 0, 0)

async def test_delete_missing_case_returns_404(client, auth_headers):
	fake_id = str(uuid.uuid4())
	response = await client.delete(f"{API}/cases/{fake_id}", headers=auth_headers)
	assert response.status_code == 404


async def test_delete_requires_auth(client):
	response = await client.delete(f"{API}/cases/{uuid.uuid4()}")
	assert response.status_code == 401
