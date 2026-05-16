"""
Unit tests for app.services.upload — validate_file().

No database or storage required. Tests cover magic-byte detection and size
validation only. Storage integration will be added once issue #4 is merged.
"""

from __future__ import annotations

import io

import pytest

pytestmark = pytest.mark.asyncio(loop_scope="session")

JPEG_BYTES = b"\xff\xd8\xff" + b"\x00" * 10
PNG_BYTES  = b"\x89PNG\r\n\x1a\n" + b"\x00" * 10
PDF_BYTES  = b"%PDF" + b"\x00" * 10
GIF_BYTES  = b"GIF89a" + b"\x00" * 10  # not allowed


async def test_accepts_jpeg():
    """JPEG magic bytes → ValidatedFile with correct filename and media type."""
    from fastapi import UploadFile

    from app.services.upload import validate_file

    upload_file = UploadFile(filename="blood.jpg", file=io.BytesIO(JPEG_BYTES))
    result = await validate_file(upload_file)

    assert result.filename == "blood.jpg"
    assert result.media_type == "image/jpeg"
    assert result.content == JPEG_BYTES


async def test_accepts_png():
    """PNG magic bytes → ValidatedFile returned."""
    from fastapi import UploadFile

    from app.services.upload import validate_file

    upload_file = UploadFile(filename="scan.png", file=io.BytesIO(PNG_BYTES))
    result = await validate_file(upload_file)

    assert result.filename == "scan.png"
    assert result.media_type == "image/png"


async def test_accepts_pdf():
    """PDF magic bytes → ValidatedFile returned."""
    from fastapi import UploadFile

    from app.services.upload import validate_file

    upload_file = UploadFile(filename="report.pdf", file=io.BytesIO(PDF_BYTES))
    result = await validate_file(upload_file)

    assert result.filename == "report.pdf"
    assert result.media_type == "application/pdf"


async def test_rejects_gif():
    """GIF magic bytes → 422 HTTPException with a helpful message."""
    from fastapi import HTTPException, UploadFile

    from app.services.upload import validate_file

    upload_file = UploadFile(filename="anim.gif", file=io.BytesIO(GIF_BYTES))

    with pytest.raises(HTTPException) as exc_info:
        await validate_file(upload_file)

    assert exc_info.value.status_code == 422
    assert "Unsupported file type" in exc_info.value.detail


async def test_rejects_oversized_file():
    """Content over 10 MB → 422 HTTPException before touching storage."""
    from fastapi import HTTPException, UploadFile

    from app.services.upload import MAX_FILE_SIZE, validate_file

    big_content = JPEG_BYTES + b"\x00" * (MAX_FILE_SIZE + 1)
    upload_file = UploadFile(filename="huge.jpg", file=io.BytesIO(big_content))

    with pytest.raises(HTTPException) as exc_info:
        await validate_file(upload_file)

    assert exc_info.value.status_code == 422
    assert "too large" in exc_info.value.detail
