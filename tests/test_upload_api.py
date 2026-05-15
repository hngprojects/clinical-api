"""
Endpoint integration tests for POST /api/v1/upload (multipart/form-data).

Storage (validate_and_upload) and the Celery pipeline are both mocked so
tests run without S3 credentials or a Celery worker. A real Postgres
database is required (see tests/conftest.py).

Service-level unit tests (no DB) live in tests/unit/test_upload_service.py.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

pytestmark = pytest.mark.asyncio(loop_scope="session")

from app.schemas.lab_result import FileObject

API = "/api/v1"
PIPELINE_TASK = "app.tasks.pipeline.run_lab_result_pipeline"
VALIDATE_AND_UPLOAD = "app.api.v1.endpoints.upload.validate_and_upload"

JPEG_BYTES = b"\xff\xd8\xff" + b"\x00" * 10
GIF_BYTES  = b"GIF89a" + b"\x00" * 10


def _file_obj(name: str = "lab.jpg", url: str = "https://storage.example.com/lab.jpg") -> FileObject:
    return FileObject(name=name, url=url)


async def test_upload_valid_file_authenticated_returns_201(client, auth_headers):
    """Valid file from an authenticated user → 201 with case_id + lab_result."""
    mock_task = MagicMock()
    with (
        patch(VALIDATE_AND_UPLOAD, new_callable=AsyncMock, return_value=_file_obj()),
        patch(PIPELINE_TASK, mock_task),
    ):
        response = await client.post(
            f"{API}/upload",
            files={"file": ("lab.jpg", JPEG_BYTES, "image/jpeg")},
            headers=auth_headers,
        )

    assert response.status_code == 201
    body = response.json()
    assert body["status"] == "success"
    assert body["message"] == "Upload received. Processing started."

    data = body["data"]
    assert "case_id" in data
    assert data["lab_result"]["ocr_status"] == "pending"
    assert data["lab_result"]["extracted_values"] is None


async def test_upload_valid_file_as_guest_returns_201(client):
    """Valid file from a guest (no token, session header) → 201."""
    mock_task = MagicMock()
    with (
        patch(VALIDATE_AND_UPLOAD, new_callable=AsyncMock, return_value=_file_obj()),
        patch(PIPELINE_TASK, mock_task),
    ):
        response = await client.post(
            f"{API}/upload",
            files={"file": ("lab.jpg", JPEG_BYTES, "image/jpeg")},
            headers={"X-Guest-Session-Id": "guest-abc-123"},
        )

    assert response.status_code == 201
    data = response.json()["data"]
    assert "case_id" in data
    assert data["lab_result"]["ocr_status"] == "pending"


async def test_upload_triggers_pipeline_exactly_once(client, auth_headers):
    """The Celery pipeline must be dispatched once with the new lab_result id."""
    mock_task = MagicMock()
    with (
        patch(VALIDATE_AND_UPLOAD, new_callable=AsyncMock, return_value=_file_obj()),
        patch(PIPELINE_TASK, mock_task),
    ):
        response = await client.post(
            f"{API}/upload",
            files={"file": ("lab.jpg", JPEG_BYTES, "image/jpeg")},
            headers=auth_headers,
        )

    assert response.status_code == 201
    lab_result_id = response.json()["data"]["lab_result"]["id"]
    mock_task.delay.assert_called_once_with(lab_result_id)


async def test_upload_ocr_status_is_pending_immediately(client, auth_headers):
    """Immediately after upload ocr_status must be 'pending' with no extracted values."""
    mock_task = MagicMock()
    with (
        patch(VALIDATE_AND_UPLOAD, new_callable=AsyncMock, return_value=_file_obj()),
        patch(PIPELINE_TASK, mock_task),
    ):
        response = await client.post(
            f"{API}/upload",
            files={"file": ("lab.jpg", JPEG_BYTES, "image/jpeg")},
            headers=auth_headers,
        )

    lab = response.json()["data"]["lab_result"]
    assert lab["ocr_status"] == "pending"
    assert lab["extracted_values"] is None
    assert lab["ocr_completed_at"] is None


async def test_upload_unsupported_file_type_returns_422(client, auth_headers):
    """When validate_and_upload raises 422, the endpoint must pass it through."""
    from fastapi import HTTPException, status

    with patch(
        VALIDATE_AND_UPLOAD,
        new_callable=AsyncMock,
        side_effect=HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Unsupported file type. Only PDF, JPG, and PNG are accepted.",
        ),
    ):
        response = await client.post(
            f"{API}/upload",
            files={"file": ("virus.gif", GIF_BYTES, "image/gif")},
            headers=auth_headers,
        )

    assert response.status_code == 422


async def test_upload_oversized_file_returns_422(client, auth_headers):
    """Files over 10 MB must be rejected with 422."""
    from fastapi import HTTPException, status

    with patch(
        VALIDATE_AND_UPLOAD,
        new_callable=AsyncMock,
        side_effect=HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="File too large. Maximum allowed size is 10 MB.",
        ),
    ):
        response = await client.post(
            f"{API}/upload",
            files={"file": ("big.jpg", JPEG_BYTES, "image/jpeg")},
            headers=auth_headers,
        )

    assert response.status_code == 422


async def test_upload_storage_failure_returns_502_no_db_records(client, auth_headers):
    """
    When the storage backend fails the endpoint must return 502.
    No MedicalCase or LabResult rows should be created.
    """
    from fastapi import HTTPException, status
    from sqlalchemy import func, select

    from app.db.session import AsyncSessionLocal
    from app.models.lab_result import LabResult
    from app.models.medical_case import MedicalCase

    async with AsyncSessionLocal() as session:
        case_count_before = (await session.execute(select(func.count()).select_from(MedicalCase))).scalar()
        lab_count_before  = (await session.execute(select(func.count()).select_from(LabResult))).scalar()

    with patch(
        VALIDATE_AND_UPLOAD,
        new_callable=AsyncMock,
        side_effect=HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="File could not be stored. Please try again.",
        ),
    ):
        response = await client.post(
            f"{API}/upload",
            files={"file": ("lab.jpg", JPEG_BYTES, "image/jpeg")},
            headers=auth_headers,
        )

    assert response.status_code == 502

    async with AsyncSessionLocal() as session:
        case_count_after = (await session.execute(select(func.count()).select_from(MedicalCase))).scalar()
        lab_count_after  = (await session.execute(select(func.count()).select_from(LabResult))).scalar()

    assert case_count_after == case_count_before
    assert lab_count_after  == lab_count_before
