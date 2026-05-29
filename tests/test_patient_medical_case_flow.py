"""
Patient-facing journey tests (history screen APIs).

There is no dedicated PHYSICIAN role in this codebase — these flows model an
authenticated patient (`UserRole.PATIENT`) creating a case, uploading a lab
report, and loading aggregated detail for the history UI.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock, patch

import pytest

from app.db.session import AsyncSessionLocal
from app.models.ai_interpretation import AIInterpretation, InterpretationStatus, RiskLevel
from app.models.chat import Chat, SenderType
from app.models.lab_result import LabResult, OCRStatus
from app.models.medical_case import MedicalCase, MedicalCaseStatus

pytestmark = pytest.mark.asyncio(loop_scope="session")

API = "/api/v1"
PIPELINE_TASK = "app.tasks.pipeline.run_lab_result_pipeline"


async def test_patient_opens_history_after_lab_upload(client, auth_headers):
	"""After creating a case and uploading a report, GET /cases/{id}/full reflects pending OCR."""
	case_resp = await client.post(f"{API}/cases", headers=auth_headers)
	assert case_resp.status_code == 201
	case_id = case_resp.json()["data"]["id"]

	mock_task = MagicMock()
	with patch(PIPELINE_TASK, mock_task):
		lab_resp = await client.post(
			f"{API}/cases/{case_id}/lab-results",
			files={"file": ("blood_panel.jpg", b"fake-image-content", "image/jpeg")},
			headers=auth_headers,
		)
	assert lab_resp.status_code == 201
	mock_task.delay.assert_called_once()

	full_resp = await client.get(f"{API}/cases/{case_id}/full", headers=auth_headers)
	assert full_resp.status_code == 200
	payload = full_resp.json()["data"]
	assert payload["case"]["id"] == case_id
	assert len(payload["lab_results"]) == 1
	assert payload["lab_results"][0]["ocr_status"] == "pending"
	assert payload["lab_results"][0]["extracted_values"] is None
	assert payload["interpretation"] is None
	assert payload["chats"] == []


async def test_patient_history_matches_chat_thread_when_ready(client, test_user, auth_headers):
	"""When OCR + interpretation and chat exist, /full matches GET .../chat ordering."""
	case_id = uuid.uuid4()
	lab_id = uuid.uuid4()
	interp_id = uuid.uuid4()
	chat_a = uuid.uuid4()
	chat_b = uuid.uuid4()
	now = datetime.now(timezone.utc)
	extras = {
		"tests": [{"name": "Hb", "value": "12", "unit": "g/dL", "reference_range": "11-15"}],
	}

	async with AsyncSessionLocal() as session:
		session.add(MedicalCase(id=case_id, user_id=test_user.id, status=MedicalCaseStatus.COMPLETE, created_at=now))
		session.add(
			LabResult(
				id=lab_id,
				medical_case_id=case_id,
				file={"name": "lab.pdf", "url": "https://example.com/lab.pdf"},
				ocr_status=OCRStatus.COMPLETE,
				extracted_values=extras,
				created_at=now,
			)
		)
		session.add(
			AIInterpretation(
				id=interp_id,
				medical_case_id=case_id,
				summary="Values look fine.",
				status=InterpretationStatus.COMPLETE,
				risk_level=RiskLevel.LOW,
				generated_at=now,
			)
		)
		session.add(
			Chat(
				id=chat_a,
				user_id=test_user.id,
				medical_case_id=case_id,
				sender_type=SenderType.PATIENT,
				content={"text": "Is this normal?"},
				sent_at=now,
			)
		)
		session.add(
			Chat(
				id=chat_b,
				user_id=test_user.id,
				medical_case_id=case_id,
				sender_type=SenderType.AI,
				content={"text": "Within range."},
				sent_at=now + timedelta(seconds=1),
			)
		)
		await session.commit()

	full_resp = await client.get(f"{API}/cases/{case_id}/full", headers=auth_headers)
	assert full_resp.status_code == 200
	full_data = full_resp.json()["data"]
	assert full_data["interpretation"] is not None
	assert full_data["interpretation"]["summary"] == "Values look fine."
	assert full_data["case"]["title"] == "Laboratory Report"
	assert len(full_data["chats"]) == 2

	thread_resp = await client.get(f"{API}/cases/{case_id}/chat", headers=auth_headers)
	assert thread_resp.status_code == 200
	thread = thread_resp.json()["data"]
	assert len(thread) == 2
	assert [m["content"]["text"] for m in thread] == [m["content"]["text"] for m in full_data["chats"]]


async def test_patient_case_list_includes_case_before_opening_full(client, test_user, auth_headers):
	"""Patient sees the new case in GET /cases before opening GET /cases/{id}/full."""
	case_resp = await client.post(f"{API}/cases", headers=auth_headers)
	assert case_resp.status_code == 201
	case_id = case_resp.json()["data"]["id"]

	update_resp = await client.patch(
		f"{API}/cases/{case_id}",
		json={"title": "Blood Panel"},
		headers=auth_headers,
	)
	assert update_resp.status_code == 200
	assert update_resp.json()["data"]["title"] == "Blood Panel"
	assert update_resp.json()["data"]["status"] == "pending"

	clear_resp = await client.patch(
		f"{API}/cases/{case_id}",
		json={"title": None, "status": "failed", "ignored": "value"},
		headers=auth_headers,
	)
	assert clear_resp.status_code == 200
	assert clear_resp.json()["data"]["title"] is None
	assert clear_resp.json()["data"]["status"] == "pending"

	rename_resp = await client.patch(
		f"{API}/cases/{case_id}",
		json={"title": "Renamed Panel", "status": "failed", "ignored": "value"},
		headers=auth_headers,
	)
	assert rename_resp.status_code == 200
	assert rename_resp.json()["data"]["title"] == "Renamed Panel"
	assert rename_resp.json()["data"]["status"] == "pending"

	list_resp = await client.get(f"{API}/cases", headers=auth_headers)
	assert list_resp.status_code == 200
	rows = list_resp.json()["data"]
	case_row = next(row for row in rows if row["id"] == case_id)
	assert case_row["title"] == "Renamed Panel"

	full_resp = await client.get(f"{API}/cases/{case_id}/full", headers=auth_headers)
	assert full_resp.status_code == 200
	full_case = full_resp.json()["data"]["case"]
	assert full_case["id"] == case_id
	assert full_case["title"] == "Renamed Panel"
