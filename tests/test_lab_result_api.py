"""
API integration tests for the lab result upload → pipeline trigger flow.

The Celery pipeline task is mocked.
"""

from __future__ import annotations

import uuid
from unittest.mock import MagicMock, patch

import pytest

pytestmark = pytest.mark.asyncio(loop_scope="session")

from app.db.session import AsyncSessionLocal
from app.models.user import User, UserRole
from app.services.auth.tokens import create_access_token

API = "/api/v1"

# Patch target: the task object in the module where .delay() is looked up
PIPELINE_TASK = "app.tasks.pipeline.run_lab_result_pipeline"



def _lab_result_file() -> dict:
    return {"file": ("blood_panel.jpg", b"fake-image-content", "image/jpeg")}



async def test_create_medical_case(client, auth_headers):
    """POST /cases → 201 with a PENDING case."""
    response = await client.post(f"{API}/cases", headers=auth_headers)

    assert response.status_code == 201
    body = response.json()
    assert body["status"] == "success"
    assert body["data"]["status"] == "pending"
    assert "id" in body["data"]


async def test_create_lab_result_returns_201_and_dispatches_pipeline(client, auth_headers):
    """
    POST /cases/{id}/lab-results → 201.

    The pipeline Celery task must be dispatched exactly once, with the
    newly created lab result's ID as its argument.
    This is the core contract: upload triggers the pipeline.
    """
    case_resp = await client.post(f"{API}/cases", headers=auth_headers)
    assert case_resp.status_code == 201
    case_id = case_resp.json()["data"]["id"]

    mock_task = MagicMock()
    with patch(PIPELINE_TASK, mock_task):
        response = await client.post(
            f"{API}/cases/{case_id}/lab-results",
            files=_lab_result_file(),
            headers=auth_headers,
        )

    assert response.status_code == 201
    body = response.json()
    assert body["status"] == "success"

    lab_result_id = body["data"]["id"]

    mock_task.delay.assert_called_once_with(lab_result_id)


async def test_lab_result_ocr_status_is_pending_immediately_after_upload(client, auth_headers):
    """
    Immediately after upload the lab result must have ocr_status = pending
    and no extracted_values yet.

    This is the polling baseline — frontend shows 'extracting values' until
    this status changes to 'complete' or 'failed'.
    """
    case_resp = await client.post(f"{API}/cases", headers=auth_headers)
    case_id = case_resp.json()["data"]["id"]

    mock_task = MagicMock()
    with patch(PIPELINE_TASK, mock_task):
        lab_resp = await client.post(
            f"{API}/cases/{case_id}/lab-results",
            files=_lab_result_file(),
            headers=auth_headers,
        )

    data = lab_resp.json()["data"]
    assert data["ocr_status"] == "pending"
    assert data["extracted_values"] is None
    assert data["ocr_completed_at"] is None


async def test_list_lab_results_for_case(client, auth_headers):
    """GET /cases/{id}/lab-results → 200 list with the uploaded result."""
    case_resp = await client.post(f"{API}/cases", headers=auth_headers)
    case_id = case_resp.json()["data"]["id"]

    mock_task = MagicMock()
    with patch(PIPELINE_TASK, mock_task):
        await client.post(
            f"{API}/cases/{case_id}/lab-results",
            files=_lab_result_file(),
            headers=auth_headers,
        )

    response = await client.get(f"{API}/cases/{case_id}/lab-results", headers=auth_headers)

    assert response.status_code == 200
    items = response.json()["data"]
    assert len(items) == 1
    assert items[0]["ocr_status"] == "pending"


async def test_retrieve_single_lab_result_by_id(client, auth_headers):
    """GET /cases/{id}/lab-results/{result_id} → 200 with the correct record."""
    case_resp = await client.post(f"{API}/cases", headers=auth_headers)
    case_id = case_resp.json()["data"]["id"]

    mock_task = MagicMock()
    with patch(PIPELINE_TASK, mock_task):
        lab_resp = await client.post(
            f"{API}/cases/{case_id}/lab-results",
            files=_lab_result_file(),
            headers=auth_headers,
        )
    result_id = lab_resp.json()["data"]["id"]

    response = await client.get(
        f"{API}/cases/{case_id}/lab-results/{result_id}",
        headers=auth_headers,
    )

    assert response.status_code == 200
    assert response.json()["data"]["id"] == result_id


async def test_interpretations_latest_is_404_before_pipeline_runs(client, auth_headers):
    """
    GET /cases/{id}/interpretations/latest → 404 immediately after upload.

    The AIInterpretation row is created by the pipeline worker, not by the upload.
    A 404 here means 'pipeline hasn't run yet' — frontend polls until it resolves.
    """
    case_resp = await client.post(f"{API}/cases", headers=auth_headers)
    case_id = case_resp.json()["data"]["id"]

    mock_task = MagicMock()
    with patch(PIPELINE_TASK, mock_task):
        await client.post(
            f"{API}/cases/{case_id}/lab-results",
            files=_lab_result_file(),
            headers=auth_headers,
        )

    response = await client.get(
        f"{API}/cases/{case_id}/interpretations/latest",
        headers=auth_headers,
    )

    assert response.status_code == 404


async def test_unauthenticated_upload_returns_401(client):
    """
    Requests without an Authorization header must be rejected with 401.
    Verifies the auth guard is active on the lab result endpoint.
    """
    fake_case_id = str(uuid.uuid4())
    response = await client.post(
        f"{API}/cases/{fake_case_id}/lab-results",
        files=_lab_result_file(),
        # no headers
    )
    assert response.status_code == 401


async def test_wrong_user_cannot_upload_to_another_users_case(client, auth_headers):
    """
    A user who did not create a case receives 404 when trying to upload to it.
    Also verifies that the pipeline task is NOT dispatched when the request is rejected.
    """
    # User A (test_user via auth_headers) creates a case
    case_resp = await client.post(f"{API}/cases", headers=auth_headers)
    assert case_resp.status_code == 201
    case_id = case_resp.json()["data"]["id"]

    # User B — a completely separate user
    other_user = User(
        id=uuid.uuid4(),
        email=f"other_{uuid.uuid4().hex[:8]}@clinsights.dev",
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

    mock_task = MagicMock()
    with patch(PIPELINE_TASK, mock_task):
        response = await client.post(
            f"{API}/cases/{case_id}/lab-results",
            files=_lab_result_file(),
            headers=other_headers,
        )

    assert response.status_code == 404
    mock_task.delay.assert_not_called()

    async with AsyncSessionLocal() as session:
        existing = await session.get(User, other_user.id)
        if existing:
            await session.delete(existing)
            await session.commit()


async def test_upload_to_nonexistent_case_returns_404(client, auth_headers):
    """
    POST /cases/{id}/lab-results with a case_id that doesn't exist → 404.
    """
    fake_case_id = str(uuid.uuid4())

    mock_task = MagicMock()
    with patch(PIPELINE_TASK, mock_task):
        response = await client.post(
            f"{API}/cases/{fake_case_id}/lab-results",
            files=_lab_result_file(),
            headers=auth_headers,
        )

    assert response.status_code == 404
    mock_task.delay.assert_not_called()
