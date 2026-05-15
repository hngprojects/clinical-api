"""
Unit tests for app.services.upload — validate_and_upload().

No database required. All storage I/O is mocked.
"""

from __future__ import annotations

import io
from unittest.mock import AsyncMock, patch

import pytest

pytestmark = pytest.mark.asyncio(loop_scope="session")

# Magic bytes for each type
JPEG_BYTES = b"\xff\xd8\xff" + b"\x00" * 10
PNG_BYTES  = b"\x89PNG\r\n\x1a\n" + b"\x00" * 10
PDF_BYTES  = b"%PDF" + b"\x00" * 10
GIF_BYTES  = b"GIF89a" + b"\x00" * 10  # not allowed


async def test_accepts_jpeg():
    """JPEG magic bytes → FileObject with correct name and url."""
    from fastapi import UploadFile

    from app.services.upload import validate_and_upload

    upload_file = UploadFile(filename="blood.jpg", file=io.BytesIO(JPEG_BYTES))

    with patch("app.services.upload._upload_local", new_callable=AsyncMock, return_value="file:///tmp/x.jpg"):
        result = await validate_and_upload(upload_file)

    assert result.name == "blood.jpg"
    assert result.url == "file:///tmp/x.jpg"


async def test_accepts_png():
    """PNG magic bytes → FileObject returned."""
    from fastapi import UploadFile

    from app.services.upload import validate_and_upload

    upload_file = UploadFile(filename="scan.png", file=io.BytesIO(PNG_BYTES))

    with patch("app.services.upload._upload_local", new_callable=AsyncMock, return_value="file:///tmp/x.png"):
        result = await validate_and_upload(upload_file)

    assert result.name == "scan.png"
    assert result.url == "file:///tmp/x.png"


async def test_accepts_pdf():
    """PDF magic bytes → FileObject returned."""
    from fastapi import UploadFile

    from app.services.upload import validate_and_upload

    upload_file = UploadFile(filename="report.pdf", file=io.BytesIO(PDF_BYTES))

    with patch("app.services.upload._upload_local", new_callable=AsyncMock, return_value="file:///tmp/x.pdf"):
        result = await validate_and_upload(upload_file)

    assert result.name == "report.pdf"
    assert result.url == "file:///tmp/x.pdf"


async def test_rejects_gif():
    """GIF magic bytes → 422 HTTPException with a helpful message."""
    from fastapi import HTTPException, UploadFile

    from app.services.upload import validate_and_upload

    upload_file = UploadFile(filename="anim.gif", file=io.BytesIO(GIF_BYTES))

    with pytest.raises(HTTPException) as exc_info:
        await validate_and_upload(upload_file)

    assert exc_info.value.status_code == 422
    assert "Unsupported file type" in exc_info.value.detail


async def test_rejects_oversized_file():
    """Content over 10 MB → 422 HTTPException before touching storage."""
    from fastapi import HTTPException, UploadFile

    from app.services.upload import MAX_FILE_SIZE, validate_and_upload

    big_content = JPEG_BYTES + b"\x00" * (MAX_FILE_SIZE + 1)
    upload_file = UploadFile(filename="huge.jpg", file=io.BytesIO(big_content))

    with pytest.raises(HTTPException) as exc_info:
        await validate_and_upload(upload_file)

    assert exc_info.value.status_code == 422
    assert "too large" in exc_info.value.detail


async def test_storage_failure_raises_502():
    """If the storage backend fails, validate_and_upload raises HTTPException(502)."""
    from fastapi import HTTPException, UploadFile

    from app.services.upload import validate_and_upload

    upload_file = UploadFile(filename="lab.jpg", file=io.BytesIO(JPEG_BYTES))

    with (
        patch("app.services.upload._upload_local", new_callable=AsyncMock, side_effect=OSError("disk full")),
        pytest.raises(HTTPException) as exc_info,
    ):
        await validate_and_upload(upload_file)

    assert exc_info.value.status_code == 502
    assert "could not be stored" in exc_info.value.detail
