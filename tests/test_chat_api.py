"""
API tests for case chat with AI replies.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from unittest.mock import AsyncMock, patch

import pytest

from app.db.session import AsyncSessionLocal
from app.models.ai_interpretation import AIInterpretation, InterpretationStatus, RiskLevel
from app.models.lab_result import LabResult, OCRStatus
from app.models.medical_case import MedicalCase, MedicalCaseStatus
from app.models.user import User

pytestmark = pytest.mark.asyncio(loop_scope="session")

API = "/api/v1"

EXTRACTED = {
	"tests": [
		{"name": "Haemoglobin", "value": "11.2", "unit": "g/dL", "reference_range": "12.0–17.5"},
	]
}

AI_REPLY = (
	"Your haemoglobin of 11.2 g/dL is below the reference range. "
	"This is not a medical diagnosis. Please consult a qualified healthcare "
	"professional for personalised advice."
)


async def _seed_ready_case(user: User) -> uuid.UUID:
	case_id = uuid.uuid4()
	lab_id = uuid.uuid4()
	interp_id = uuid.uuid4()
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
				file={"name": "panel.jpg", "url": "https://example.com/panel.jpg"},
				ocr_status=OCRStatus.COMPLETE,
				extracted_values=EXTRACTED,
				created_at=now,
			)
		)
		session.add(
			AIInterpretation(
				id=interp_id,
				medical_case_id=case_id,
				summary="Haemoglobin is below the normal range.",
				status=InterpretationStatus.COMPLETE,
				risk_level=RiskLevel.MODERATE,
				generated_at=now,
			)
		)
		await session.commit()

	return case_id


@patch("app.services.chat.generate_chat_response", new_callable=AsyncMock, return_value=AI_REPLY)
async def test_send_message_returns_user_and_ai_reply(mock_generate, client, test_user, auth_headers):
	case_id = await _seed_ready_case(test_user)

	response = await client.post(
		f"{API}/cases/{case_id}/chat",
		json={"text": "What does my haemoglobin mean?"},
		headers=auth_headers,
	)

	assert response.status_code == 201
	body = response.json()
	assert body["status"] == "success"
	data = body["data"]
	assert data["user_message"]["sender_type"] == "patient"
	assert data["user_message"]["content"]["text"] == "What does my haemoglobin mean?"
	assert data["ai_message"]["sender_type"] == "ai"
	assert "haemoglobin" in data["ai_message"]["content"]["text"].lower()
	mock_generate.assert_awaited_once()


@patch("app.services.chat.generate_chat_response", new_callable=AsyncMock, return_value=AI_REPLY)
async def test_chat_maintains_history_across_messages(mock_generate, client, test_user, auth_headers):
	case_id = await _seed_ready_case(test_user)

	first = await client.post(
		f"{API}/cases/{case_id}/chat",
		json={"text": "First question"},
		headers=auth_headers,
	)
	assert first.status_code == 201
	second = await client.post(
		f"{API}/cases/{case_id}/chat",
		json={"text": "Second question"},
		headers=auth_headers,
	)
	assert second.status_code == 201

	list_resp = await client.get(f"{API}/cases/{case_id}/chat", headers=auth_headers)
	assert list_resp.status_code == 200
	messages = list_resp.json()["data"]
	assert len(messages) == 4
	assert messages[0]["content"]["text"] == "First question"
	assert messages[2]["content"]["text"] == "Second question"
	assert mock_generate.await_count == 2


async def test_chat_before_lab_ready_returns_409(client, test_user, auth_headers):
	case_resp = await client.post(f"{API}/cases", headers=auth_headers)
	assert case_resp.status_code == 201
	case_id = case_resp.json()["data"]["id"]

	response = await client.post(
		f"{API}/cases/{case_id}/chat",
		json={"text": "Hello?"},
		headers=auth_headers,
	)

	assert response.status_code == 409


async def test_chat_empty_text_returns_422(client, test_user, auth_headers):
	case_id = await _seed_ready_case(test_user)

	response = await client.post(
		f"{API}/cases/{case_id}/chat",
		json={"text": ""},
		headers=auth_headers,
	)

	assert response.status_code == 422


async def test_chat_whitespace_only_text_returns_422(client, test_user, auth_headers):
	case_id = await _seed_ready_case(test_user)

	response = await client.post(
		f"{API}/cases/{case_id}/chat",
		json={"text": "   \t\n  "},
		headers=auth_headers,
	)

	assert response.status_code == 422
