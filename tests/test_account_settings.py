"""Tests for PATCH /users/me, PATCH /users/me/password, DELETE /users/me."""
from __future__ import annotations

import uuid

import pytest

from app.core.security import hash_password, verify_password
from app.db.session import AsyncSessionLocal
from app.models.token_blocklist import TokenBlocklist
from app.models.user import User, UserRole
from app.services.auth.tokens import create_access_token

pytestmark = pytest.mark.asyncio(loop_scope="session")

API = "/api/v1"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

async def _create_user(*, email: str, password: str = "Password123!") -> User:
    user = User(
        id=uuid.uuid4(),
        email=email,
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


async def _get_user(user_id: uuid.UUID) -> User | None:
    async with AsyncSessionLocal() as session:
        return await session.get(User, user_id)


async def _is_token_blocklisted(jti: str) -> bool:
    from sqlalchemy import select
    async with AsyncSessionLocal() as session:
        result = await session.execute(
            select(TokenBlocklist).where(TokenBlocklist.jti == jti).limit(1)
        )
        return result.scalar_one_or_none() is not None


def _auth_headers(user_id: uuid.UUID) -> dict[str, str]:
    token, _ = create_access_token(user_id)
    return {"Authorization": f"Bearer {token}"}


def _auth_headers_with_token(user_id: uuid.UUID) -> tuple[dict[str, str], str]:
    """Return (headers, raw_token) so the token can be inspected after the request."""
    import jwt as pyjwt

    from app.core.config import get_settings
    settings = get_settings()
    token, _ = create_access_token(user_id)
    payload = pyjwt.decode(token, settings.JWT_SECRET, algorithms=[settings.JWT_ALGORITHM])
    return {"Authorization": f"Bearer {token}"}, payload["jti"]


def _headers_with_refresh_cookie(headers: dict[str, str], refresh_token: str) -> dict[str, str]:
    """Merge Authorization headers with a refresh_token Cookie header."""
    return {**headers, "Cookie": f"refresh_token={refresh_token}"}


# ---------------------------------------------------------------------------
# PATCH /users/me — profile update
# ---------------------------------------------------------------------------

async def test_update_profile_success(client) -> None:
    user = await _create_user(email=f"profile_{uuid.uuid4().hex[:8]}@clinsights.dev")
    try:
        response = await client.patch(
            f"{API}/users/me",
            json={"first_name": "Updated", "last_name": "Name"},
            headers=_auth_headers(user.id),
        )
        assert response.status_code == 200
        body = response.json()
        assert body["status"] == "success"
        assert body["data"]["first_name"] == "Updated"
        assert body["data"]["last_name"] == "Name"

        db_user = await _get_user(user.id)
        assert db_user is not None
        assert db_user.first_name == "Updated"
        assert db_user.last_name == "Name"
    finally:
        await _delete_user(user.id)


async def test_update_profile_partial_only_first_name(client) -> None:
    user = await _create_user(email=f"profile_{uuid.uuid4().hex[:8]}@clinsights.dev")
    try:
        response = await client.patch(
            f"{API}/users/me",
            json={"first_name": "OnlyFirst"},
            headers=_auth_headers(user.id),
        )
        assert response.status_code == 200
        body = response.json()
        assert body["data"]["first_name"] == "OnlyFirst"
        assert body["data"]["last_name"] == "User"  # unchanged
    finally:
        await _delete_user(user.id)


async def test_update_profile_empty_body_returns_200_no_change(client) -> None:
    user = await _create_user(email=f"profile_{uuid.uuid4().hex[:8]}@clinsights.dev")
    try:
        response = await client.patch(
            f"{API}/users/me",
            json={},
            headers=_auth_headers(user.id),
        )
        assert response.status_code == 200
        body = response.json()
        assert body["data"]["first_name"] == "Test"
        assert body["data"]["last_name"] == "User"
    finally:
        await _delete_user(user.id)


async def test_update_profile_unauthenticated_returns_401(client) -> None:
    response = await client.patch(f"{API}/users/me", json={"first_name": "X"})
    assert response.status_code == 401


# ---------------------------------------------------------------------------
# PATCH /users/me/password — password change
# ---------------------------------------------------------------------------

async def test_update_password_success(client) -> None:
    user = await _create_user(
        email=f"pwd_{uuid.uuid4().hex[:8]}@clinsights.dev",
        password="OldPassword1!",
    )
    try:
        response = await client.patch(
            f"{API}/users/me/password",
            json={"current_password": "OldPassword1!", "new_password": "NewPassword2!"},
            headers=_auth_headers(user.id),
        )
        assert response.status_code == 200
        assert response.json()["status"] == "success"

        db_user = await _get_user(user.id)
        assert db_user is not None
        assert db_user.password_hash is not None
        assert verify_password("NewPassword2!", db_user.password_hash)
    finally:
        await _delete_user(user.id)


async def test_update_password_wrong_current_returns_400(client) -> None:
    user = await _create_user(email=f"pwd_{uuid.uuid4().hex[:8]}@clinsights.dev")
    try:
        response = await client.patch(
            f"{API}/users/me/password",
            json={"current_password": "WrongPassword!", "new_password": "NewPassword2!"},
            headers=_auth_headers(user.id),
        )
        assert response.status_code == 400
        assert response.json()["message"] == "Incorrect password"
    finally:
        await _delete_user(user.id)


async def test_update_password_too_short_returns_422(client) -> None:
    user = await _create_user(email=f"pwd_{uuid.uuid4().hex[:8]}@clinsights.dev")
    try:
        response = await client.patch(
            f"{API}/users/me/password",
            json={"current_password": "Password123!", "new_password": "short"},
            headers=_auth_headers(user.id),
        )
        assert response.status_code == 422
    finally:
        await _delete_user(user.id)


async def test_update_password_unauthenticated_returns_401(client) -> None:
    response = await client.patch(
        f"{API}/users/me/password",
        json={"current_password": "Password123!", "new_password": "NewPassword2!"},
    )
    assert response.status_code == 401


async def test_update_password_invalidates_access_token(client) -> None:
    user = await _create_user(
        email=f"pwd_{uuid.uuid4().hex[:8]}@clinsights.dev",
        password="OldPassword1!",
    )
    try:
        headers, jti = _auth_headers_with_token(user.id)
        response = await client.patch(
            f"{API}/users/me/password",
            json={"current_password": "OldPassword1!", "new_password": "NewPassword2!"},
            headers=headers,
        )
        assert response.status_code == 200

        assert await _is_token_blocklisted(jti)

        me_resp = await client.get(f"{API}/auth/me", headers=headers)
        assert me_resp.status_code == 401
    finally:
        await _delete_user(user.id)


async def test_update_password_blocklists_refresh_token(client) -> None:
    import jwt as pyjwt

    from app.core.config import get_settings
    from app.services.auth.tokens import create_refresh_token

    user = await _create_user(
        email=f"pwd_{uuid.uuid4().hex[:8]}@clinsights.dev",
        password="OldPassword1!",
    )
    try:
        headers, _ = _auth_headers_with_token(user.id)
        refresh_token = await create_refresh_token(user.id)
        settings = get_settings()
        refresh_payload = pyjwt.decode(
            refresh_token, settings.JWT_SECRET, algorithms=[settings.JWT_ALGORITHM]
        )
        refresh_jti = refresh_payload["jti"]

        response = await client.patch(
            f"{API}/users/me/password",
            json={"current_password": "OldPassword1!", "new_password": "NewPassword2!"},
            headers=_headers_with_refresh_cookie(headers, refresh_token),
        )
        assert response.status_code == 200
        assert await _is_token_blocklisted(refresh_jti)
    finally:
        await _delete_user(user.id)


# ---------------------------------------------------------------------------
# DELETE /users/me — account deletion
# ---------------------------------------------------------------------------

async def test_delete_account_success(client) -> None:
    user = await _create_user(email=f"del_{uuid.uuid4().hex[:8]}@clinsights.dev")
    user_id = user.id
    headers, _ = _auth_headers_with_token(user.id)

    response = await client.delete(f"{API}/users/me", headers=headers)
    assert response.status_code == 200
    assert response.json()["status"] == "success"

    db_user = await _get_user(user_id)
    assert db_user is None


async def test_delete_account_blocklists_token(client) -> None:
    user = await _create_user(email=f"del_{uuid.uuid4().hex[:8]}@clinsights.dev")
    headers, jti = _auth_headers_with_token(user.id)

    response = await client.delete(f"{API}/users/me", headers=headers)
    assert response.status_code == 200

    assert await _is_token_blocklisted(jti)


async def test_delete_account_token_rejected_on_subsequent_request(client) -> None:
    user = await _create_user(email=f"del_{uuid.uuid4().hex[:8]}@clinsights.dev")
    headers, _ = _auth_headers_with_token(user.id)

    delete_resp = await client.delete(f"{API}/users/me", headers=headers)
    assert delete_resp.status_code == 200

    # Same token must now be rejected
    me_resp = await client.get(f"{API}/auth/me", headers=headers)
    assert me_resp.status_code == 401


async def test_delete_account_unauthenticated_returns_401(client) -> None:
    response = await client.delete(f"{API}/users/me")
    assert response.status_code == 401


async def test_delete_account_blocklists_refresh_token(client) -> None:
    import jwt as pyjwt

    from app.core.config import get_settings
    from app.services.auth.tokens import create_refresh_token

    user = await _create_user(email=f"del_{uuid.uuid4().hex[:8]}@clinsights.dev")
    headers, _ = _auth_headers_with_token(user.id)
    refresh_token = await create_refresh_token(user.id)
    settings = get_settings()
    refresh_payload = pyjwt.decode(
        refresh_token, settings.JWT_SECRET, algorithms=[settings.JWT_ALGORITHM]
    )
    refresh_jti = refresh_payload["jti"]

    response = await client.delete(
        f"{API}/users/me",
        headers=_headers_with_refresh_cookie(headers, refresh_token),
    )
    assert response.status_code == 200
    assert await _is_token_blocklisted(refresh_jti)
