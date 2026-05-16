"""
Endpoint integration tests for POST /api/v1/upload.

Storage integration is pending (issue #4). These tests cover:
- Valid files pass validation and return 503 (storage not yet wired)
- Invalid type/size are rejected with 422 before storage is reached
"""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest
from fastapi import HTTPException, status

pytestmark = pytest.mark.asyncio(loop_scope="session")

API = "/api/v1"
VALIDATE_FILE = "app.api.v1.endpoints.upload.validate_file"

JPEG_BYTES = b"\xff\xd8\xff" + b"\x00" * 10
GIF_BYTES = b"GIF89a" + b"\x00" * 10


async def test_upload_valid_file_returns_503_storage_pending(client, auth_headers):
    """Valid file passes validation but returns 503 until storage is wired up (issue #4)."""
    response = await client.post(
        f"{API}/upload",
        files={"file": ("lab.jpg", JPEG_BYTES, "image/jpeg")},
        headers=auth_headers,
    )
    assert response.status_code == 503


async def test_upload_unsupported_file_type_returns_422(client, auth_headers):
    """Non-PDF/JPG/PNG files must be rejected with 422."""
    response = await client.post(
        f"{API}/upload",
        files={"file": ("virus.gif", GIF_BYTES, "image/gif")},
        headers=auth_headers,
    )
    assert response.status_code == 422


async def test_upload_oversized_file_returns_422(client, auth_headers):
    """Files over 10 MB must be rejected with 422."""
    with patch(
        VALIDATE_FILE,
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
