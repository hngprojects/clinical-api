"""Tests for PATCH /users/me/avatar — profile picture upload."""
from __future__ import annotations

import uuid
from unittest.mock import AsyncMock, patch

import pytest

from app.core.security import hash_password
from app.db.session import AsyncSessionLocal
from app.models.user import User, UserRole
from app.services.auth.tokens import create_access_token

pytestmark = pytest.mark.asyncio(loop_scope="session")

API = "/api/v1"

# Patch the storage functions at the importing module (users.py) because they
# are module-level imports, not lazy function-level imports.
_STORAGE_UPLOAD_PATCH = "app.api.v1.endpoints.users.upload_medical_file"
_STORAGE_DELETE_PATCH = "app.api.v1.endpoints.users.delete_medical_file_by_url"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

async def _create_user(*, email: str | None = None, password: str = "Password123!") -> User:
    user = User(
        id=uuid.uuid4(),
        email=email or f"avatar_{uuid.uuid4().hex[:8]}@clinsights.dev",
        first_name="Test",
        last_name="User",
        role=UserRole.PATIENT,
        is_active=True,
        is_email_verified=True,
        password_hash=hash_password(password),
    )
    async with AsyncSessionLocal() as session:
        session.add(user)
        await session.commit()
        await session.refresh(user)
    return user


async def _delete_user(user_id: uuid.UUID) -> None:
    async with AsyncSessionLocal() as session:
        user = await session.get(User, user_id)
        if user:
            await session.delete(user)
            await session.commit()


def _auth_headers(user_id: uuid.UUID) -> dict[str, str]:
    token, _ = create_access_token(user_id)
    return {"Authorization": f"Bearer {token}"}


def _fake_upload_result(filename: str, mime_type: str) -> dict:
    return {
        "filename": filename,
        "file_url": f"http://test/media/{filename}",
        "mime_type": mime_type,
        "file_size": 1024,
    }


# ---------------------------------------------------------------------------
# PATCH /users/me/avatar — avatar upload
# ---------------------------------------------------------------------------


async def test_upload_avatar_success(client) -> None:
    """Upload a valid JPEG image and verify the avatar_url is stored."""
    user = await _create_user()
    try:
        with patch(_STORAGE_UPLOAD_PATCH, new_callable=AsyncMock,
                   return_value=_fake_upload_result("avatar.jpg", "image/jpeg")):
            response = await client.patch(
                f"{API}/users/me/avatar",
                files={"file": ("avatar.jpg", b"fake-jpeg-content", "image/jpeg")},
                headers=_auth_headers(user.id),
            )
        assert response.status_code == 200
        body = response.json()
        assert body["status"] == "success"
        assert body["message"] == "Profile picture updated successfully"
        assert body["data"]["avatar_url"] == "http://test/media/avatar.jpg"
    finally:
        await _delete_user(user.id)


async def test_upload_avatar_replaces_old_avatar(client) -> None:
    """Upload a new avatar and verify the old one is replaced and old file deleted."""
    user = await _create_user()
    # Set an existing avatar_url so it gets replaced.
    async with AsyncSessionLocal() as session:
        db_user = await session.get(User, user.id)
        db_user.avatar_url = "http://test/media/old_avatar.jpg"
        await session.commit()

    try:
        with (
            patch(_STORAGE_UPLOAD_PATCH, new_callable=AsyncMock,
                  return_value=_fake_upload_result("new_avatar.jpg", "image/jpeg")),
            patch(_STORAGE_DELETE_PATCH) as mock_delete,
        ):
            response = await client.patch(
                f"{API}/users/me/avatar",
                files={"file": ("new_avatar.jpg", b"fake-jpeg-content", "image/jpeg")},
                headers=_auth_headers(user.id),
            )
        assert response.status_code == 200
        body = response.json()
        assert body["data"]["avatar_url"] == "http://test/media/new_avatar.jpg"
        mock_delete.assert_called_once_with("http://test/media/old_avatar.jpg")
    finally:
        await _delete_user(user.id)


async def test_upload_avatar_no_file_returns_422(client) -> None:
    """Sending the request without a file should return 422."""
    user = await _create_user()
    try:
        response = await client.patch(
            f"{API}/users/me/avatar",
            headers=_auth_headers(user.id),
        )
        assert response.status_code == 422
    finally:
        await _delete_user(user.id)


async def test_upload_avatar_invalid_file_type_returns_400(client) -> None:
    """Uploading a non-image file type should return 400."""
    user = await _create_user()
    try:
        response = await client.patch(
            f"{API}/users/me/avatar",
            files={"file": ("document.pdf", b"%PDF-fake-content", "application/pdf")},
            headers=_auth_headers(user.id),
        )
        assert response.status_code == 400
        body = response.json()
        assert "Unsupported file type" in body["message"]
    finally:
        await _delete_user(user.id)


async def test_upload_avatar_file_too_large_returns_400(client) -> None:
    """Uploading a file larger than 5 MB should return 400."""
    user = await _create_user()
    oversized = b"x" * (5 * 1024 * 1024 + 1)
    try:
        response = await client.patch(
            f"{API}/users/me/avatar",
            files={"file": ("large.jpg", oversized, "image/jpeg")},
            headers=_auth_headers(user.id),
        )
        assert response.status_code == 400
        body = response.json()
        assert "File size must be 5MB or smaller" in body["message"]
    finally:
        await _delete_user(user.id)


async def test_upload_avatar_unauthenticated_returns_401(client) -> None:
    """Request without auth header should return 401."""
    response = await client.patch(
        f"{API}/users/me/avatar",
        files={"file": ("avatar.jpg", b"fake-jpeg-content", "image/jpeg")},
    )
    assert response.status_code == 401