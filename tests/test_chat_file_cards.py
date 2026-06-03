"""
Test that uploaded files (images and PDFs) appear as file-card chat messages
in the unified chat history timeline.

Flow tested:
  1. Upload a lab result file (image and PDF separately)
  2. GET /cases/{case_id}/chat
  3. Assert the response contains a message with sender_type="file"
     and the expected file metadata (name, url, mime_type)
"""

from __future__ import annotations

import uuid
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

pytestmark = pytest.mark.asyncio(loop_scope="session")

API = "/api/v1"

# upload_medical_file is imported inside each function body in lab_result.py
# (e.g. `from app.services.storage import upload_medical_file`), so it never
# exists as a module-level attribute on app.services.lab_result.
# Patch the definition site instead — that's where the name always lives.
_STORAGE_PATCH = "app.services.storage.upload_medical_file"
_PIPELINE_PATCH = "app.tasks.pipeline.run_lab_result_pipeline"


def _fake_upload(filename: str, mime_type: str) -> dict:
    """Mirrors the real dict that upload_medical_file returns.
    Keys must match exactly what lab_result.py reads:
      file_metadata["filename"], file_metadata["file_url"], file_metadata["mime_type"]
    """
    return {
        "filename": filename,
        "file_url": f"http://testserver/media/{filename}",
        "mime_type": mime_type,
    }


async def test_upload_image_creates_file_card_in_chat(client, test_user, auth_headers):
    """Upload a JPEG image → Chat history includes a file-card message."""
    # 1. Create a case
    case_resp = await client.post(f"{API}/cases", headers=auth_headers)
    assert case_resp.status_code == 201
    case_id = case_resp.json()["data"]["id"]

    # 2. Upload an image (mock storage + Celery so no Redis/FS needed)
    mock_pipeline = MagicMock()
    mock_pipeline.delay = MagicMock()
    with (
        patch(_STORAGE_PATCH, new_callable=AsyncMock,
              return_value=_fake_upload("blood_panel.jpg", "image/jpeg")),
        patch(_PIPELINE_PATCH, mock_pipeline),
    ):
        resp = await client.post(
            f"{API}/cases/{case_id}/lab-results",
            files={"file": ("blood_panel.jpg", b"fake-image-content", "image/jpeg")},
            headers=auth_headers,
        )
    assert resp.status_code == 201

    # 3. Fetch chat history
    chat_resp = await client.get(f"{API}/cases/{case_id}/chat", headers=auth_headers)
    assert chat_resp.status_code == 200

    messages = chat_resp.json()["data"]
    assert len(messages) >= 1

    file_messages = [m for m in messages if m["sender_type"] == "file"]
    assert len(file_messages) >= 1, (
        f"No file-card message found in chat history. Messages: {messages}"
    )

    file_msg = file_messages[0]
    assert file_msg["file"]["name"] == "blood_panel.jpg"
    assert file_msg["file"]["mime_type"] == "image/jpeg"
    assert file_msg["file"]["url"].startswith("http://")
    assert file_msg["content"] == {"text": ""}
    assert file_msg["sender_type"] == "file"


async def test_upload_pdf_creates_file_card_in_chat(client, test_user, auth_headers):
    """Upload a PDF → Chat history includes a file-card message."""
    case_resp = await client.post(f"{API}/cases", headers=auth_headers)
    assert case_resp.status_code == 201
    case_id = case_resp.json()["data"]["id"]

    mock_pipeline = MagicMock()
    mock_pipeline.delay = MagicMock()
    with (
        patch(_STORAGE_PATCH, new_callable=AsyncMock,
              return_value=_fake_upload("report.pdf", "application/pdf")),
        patch(_PIPELINE_PATCH, mock_pipeline),
    ):
        resp = await client.post(
            f"{API}/cases/{case_id}/lab-results",
            files={"file": ("report.pdf", b"%PDF-fake-content", "application/pdf")},
            headers=auth_headers,
        )
    assert resp.status_code == 201

    chat_resp = await client.get(f"{API}/cases/{case_id}/chat", headers=auth_headers)
    assert chat_resp.status_code == 200

    messages = chat_resp.json()["data"]
    file_messages = [m for m in messages if m["sender_type"] == "file"]
    assert len(file_messages) >= 1

    file_msg = file_messages[0]
    assert file_msg["file"]["name"] == "report.pdf"
    assert file_msg["file"]["mime_type"] == "application/pdf"
    assert file_msg["file"]["url"].startswith("http://")


async def test_upload_creates_file_card_via_upload_endpoint(client, test_user, auth_headers):
    """POST /upload → the chat history also gets a file-card message."""
    mock_pipeline = MagicMock()
    mock_pipeline.delay = MagicMock()
    with (
        patch(_STORAGE_PATCH, new_callable=AsyncMock,
              return_value=_fake_upload("xray.png", "image/png")),
        patch(_PIPELINE_PATCH, mock_pipeline),
    ):
        resp = await client.post(
            f"{API}/upload",
            files={"file": ("xray.png", b"fake-png-content", "image/png")},
            headers=auth_headers,
        )
    assert resp.status_code == 201
    case_id = resp.json()["data"]["case_id"]

    chat_resp = await client.get(f"{API}/cases/{case_id}/chat", headers=auth_headers)
    assert chat_resp.status_code == 200

    messages = chat_resp.json()["data"]
    file_messages = [m for m in messages if m["sender_type"] == "file"]
    assert len(file_messages) >= 1

    file_msg = file_messages[0]
    assert file_msg["file"]["mime_type"] in ("image/png", "image/jpeg")
    assert file_msg["file"]["name"].endswith(".png") or file_msg["file"]["name"].endswith(".jpg")


async def test_file_card_appears_in_unified_timeline(client, test_user, auth_headers):
    """File cards and text messages appear interleaved by sent_at."""
    case_resp = await client.post(f"{API}/cases", headers=auth_headers)
    assert case_resp.status_code == 201
    case_id = case_resp.json()["data"]["id"]

    mock_pipeline = MagicMock()
    mock_pipeline.delay = MagicMock()
    with (
        patch(_STORAGE_PATCH, new_callable=AsyncMock,
              return_value=_fake_upload("lab.jpg", "image/jpeg")),
        patch(_PIPELINE_PATCH, mock_pipeline),
    ):
        resp = await client.post(
            f"{API}/cases/{case_id}/lab-results",
            files={"file": ("lab.jpg", b"fake-image", "image/jpeg")},
            headers=auth_headers,
        )
    assert resp.status_code == 201

    msg_resp = await client.post(
        f"{API}/cases/{case_id}/chat",
        json={
            "sender_type": "patient",
            "content": {"text": "What do my results mean?"},
            "medical_case_id": str(case_id),
        },
        headers=auth_headers,
    )
    assert msg_resp.status_code == 201

    chat_resp = await client.get(f"{API}/cases/{case_id}/chat", headers=auth_headers)
    assert chat_resp.status_code == 200

    messages = chat_resp.json()["data"]
    sender_types = [m["sender_type"] for m in messages]
    assert "file" in sender_types
    assert "patient" in sender_types

    timestamps = [m["sent_at"] for m in messages]
    assert timestamps == sorted(timestamps), "Messages should be in chronological order"


async def test_chat_endpoint_is_sufficient_alone(client, test_user, auth_headers):
    """No endpoint merging needed: chat history alone provides all file info."""
    case_resp = await client.post(f"{API}/cases", headers=auth_headers)
    case_id = case_resp.json()["data"]["id"]

    mock_pipeline = MagicMock()
    mock_pipeline.delay = MagicMock()
    with (
        patch(_STORAGE_PATCH, new_callable=AsyncMock,
              return_value=_fake_upload("test.jpg", "image/jpeg")),
        patch(_PIPELINE_PATCH, mock_pipeline),
    ):
        await client.post(
            f"{API}/cases/{case_id}/lab-results",
            files={"file": ("test.jpg", b"fake", "image/jpeg")},
            headers=auth_headers,
        )

    chat_resp = await client.get(f"{API}/cases/{case_id}/chat", headers=auth_headers)
    assert chat_resp.status_code == 200

    data = chat_resp.json()["data"]
    assert len(data) >= 1

    # Explicitly assert at least one file-card message exists (avoids vacuous pass)
    file_messages = [m for m in data if m["sender_type"] == "file"]
    assert len(file_messages) >= 1, (
        f"Expected at least one file-card message, got none. Messages: {data}"
    )

    # Every file message has all required fields
    for msg in file_messages:
        f = msg["file"]
        assert "name" in f and isinstance(f["name"], str)
        assert "url" in f and isinstance(f["url"], str)
        assert "mime_type" in f and isinstance(f["mime_type"], str)
        assert f["mime_type"] in (
            "image/jpeg", "image/png", "image/webp", "application/pdf"
        )


async def test_guest_upload_also_creates_file_card(client, test_user, auth_headers):
    """Guest users uploading files also get file-card messages in chat."""
    case_resp = await client.post(f"{API}/cases", headers=auth_headers)
    case_id = case_resp.json()["data"]["id"]

    mock_pipeline = MagicMock()
    mock_pipeline.delay = MagicMock()
    with (
        patch(_STORAGE_PATCH, new_callable=AsyncMock,
              return_value=_fake_upload("guest_scan.jpg", "image/jpeg")),
        patch(_PIPELINE_PATCH, mock_pipeline),
    ):
        await client.post(
            f"{API}/cases/{case_id}/lab-results",
            files={"file": ("guest_scan.jpg", b"fake-guest", "image/jpeg")},
            headers=auth_headers,
        )

    chat_resp = await client.get(f"{API}/cases/{case_id}/chat", headers=auth_headers)
    file_msgs = [m for m in chat_resp.json()["data"] if m["sender_type"] == "file"]
    assert len(file_msgs) == 1
    assert file_msgs[0]["file"]["name"] == "guest_scan.jpg"