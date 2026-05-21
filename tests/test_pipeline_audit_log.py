"""Tests for the pipeline-log endpoint and state machine transition guards."""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.db.session import AsyncSessionLocal
from app.models.lab_result import LabResult, OCRStatus
from app.models.medical_case import MedicalCase, MedicalCaseStatus
from app.models.pipeline_audit_log import PipelineAuditLog
from app.models.user import User, UserRole
from app.services.auth.tokens import create_access_token

pytestmark = pytest.mark.asyncio(loop_scope="session")

API = "/api/v1"


# ── Helpers ─────────────────────────────────────────────────────────────


async def _seed_case_with_logs(user: User) -> tuple[uuid.UUID, uuid.UUID]:
    """Create a case, lab result, and two audit log entries."""
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
                file={"name": "panel.jpg", "url": "https://example.com/panel.jpg"},
                ocr_status=OCRStatus.COMPLETE,
                created_at=now,
            )
        )
        session.add(
            PipelineAuditLog(
                lab_result_id=lab_id,
                event="PIPELINE_STARTED",
                status_after="processing",
                created_at=now,
            )
        )
        session.add(
            PipelineAuditLog(
                lab_result_id=lab_id,
                event="OCR_COMPLETE",
                status_before="processing",
                status_after="complete",
                created_at=now,
            )
        )
        await session.commit()

    return case_id, lab_id


# ── Endpoint tests ───────────────────────────────────────────────────────


async def test_pipeline_log_returns_entries_for_owner(client, test_user, auth_headers):
    case_id, _ = await _seed_case_with_logs(test_user)

    resp = await client.get(f"{API}/cases/{case_id}/pipeline-log", headers=auth_headers)
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "success"
    logs = body["data"]
    assert len(logs) == 2
    assert logs[0]["event"] == "PIPELINE_STARTED"
    assert logs[1]["event"] == "OCR_COMPLETE"


async def test_pipeline_log_returns_404_for_missing_case(client, auth_headers):
    resp = await client.get(f"{API}/cases/{uuid.uuid4()}/pipeline-log", headers=auth_headers)
    assert resp.status_code == 404


async def test_pipeline_log_returns_404_for_wrong_user(client, test_user):
    case_id, _ = await _seed_case_with_logs(test_user)

    other = User(
        id=uuid.uuid4(),
        email=f"audit_other_{uuid.uuid4().hex[:8]}@clinsights.dev",
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
        f"{API}/cases/{case_id}/pipeline-log",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 404

    async with AsyncSessionLocal() as session:
        existing = await session.get(User, other.id)
        if existing:
            await session.delete(existing)
            await session.commit()


# ── State machine guard tests ────────────────────────────────────────────


async def test_valid_ocr_transition_proceeds():
    """PENDING → processing is a valid OCR transition and should update status."""
    from app.tasks.pipeline import _set_ocr_status

    mock_lr = MagicMock()
    mock_lr.ocr_status = OCRStatus.PENDING

    mock_session = AsyncMock()
    mock_session.get = AsyncMock(return_value=mock_lr)

    await _set_ocr_status(mock_session, uuid.uuid4(), "processing")

    assert mock_lr.ocr_status == OCRStatus.PROCESSING
    mock_session.commit.assert_called_once()


async def test_invalid_ocr_transition_blocked():
    """COMPLETE → processing is invalid; status must not change."""
    from app.tasks.pipeline import _set_ocr_status

    mock_lr = MagicMock()
    mock_lr.ocr_status = OCRStatus.COMPLETE

    mock_session = AsyncMock()
    mock_session.get = AsyncMock(return_value=mock_lr)

    await _set_ocr_status(mock_session, uuid.uuid4(), "processing")

    assert mock_lr.ocr_status == OCRStatus.COMPLETE
    mock_session.commit.assert_not_called()


async def test_valid_case_transition_proceeds():
    """PENDING → complete is a valid case transition."""
    from app.tasks.pipeline import _set_case_status

    mock_case = MagicMock()
    mock_case.status = MedicalCaseStatus.PENDING

    mock_session = AsyncMock()
    mock_session.get = AsyncMock(return_value=mock_case)

    await _set_case_status(mock_session, uuid.uuid4(), "complete")

    assert mock_case.status == MedicalCaseStatus.COMPLETE
    mock_session.commit.assert_called_once()


async def test_invalid_case_transition_blocked():
    """COMPLETE → processing is invalid; status must not change."""
    from app.tasks.pipeline import _set_case_status

    mock_case = MagicMock()
    mock_case.status = MedicalCaseStatus.COMPLETE

    mock_session = AsyncMock()
    mock_session.get = AsyncMock(return_value=mock_case)

    await _set_case_status(mock_session, uuid.uuid4(), "processing")

    assert mock_case.status == MedicalCaseStatus.COMPLETE
    mock_session.commit.assert_not_called()
