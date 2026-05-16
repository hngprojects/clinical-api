from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

import pytest
from sqlalchemy import select

from app.db.session import AsyncSessionLocal
from app.models.otp import OtpCode, OtpPurpose
from app.models.user import User, UserRole
from app.services.auth.tokens import create_access_token
from app.core.security import hash_password

pytestmark = pytest.mark.asyncio(loop_scope="session")


API = "/api/v1"


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


def _auth_headers(user_id: uuid.UUID) -> dict[str, str]:
    token, _ = create_access_token(user_id)
    return {"Authorization": f"Bearer {token}"}


async def _latest_otp_for_user(user_id: uuid.UUID) -> OtpCode | None:
    async with AsyncSessionLocal() as session:
        result = await session.execute(
            select(OtpCode)
            .where(OtpCode.user_id == user_id, OtpCode.purpose == OtpPurpose.EMAIL_VERIFICATION)
            .order_by(OtpCode.created_at.desc())
            .limit(1)
        )
        return result.scalar_one_or_none()


async def _get_user(user_id: uuid.UUID) -> User | None:
    async with AsyncSessionLocal() as session:
        return await session.get(User, user_id)


async def test_request_email_update_success(client) -> None:
    user = await _create_user(email=f"current_{uuid.uuid4().hex[:8]}@clinsights.dev")
    try:
        with patch("app.api.v1.endpoints.users.send_otp_email_task.delay") as mock_delay:
            response = await client.post(
                f"{API}/users/me/email",
                json={"email": f"new_{uuid.uuid4().hex[:8]}@clinsights.dev", "password": "Password123!"},
                headers=_auth_headers(user.id),
            )

        assert response.status_code == 200
        body = response.json()
        assert body["status"] == "success"
        assert body["message"] == "Verification email sent to new address"
        mock_delay.assert_called_once()

        updated = await _get_user(user.id)
        assert updated is not None
        assert updated.pending_email is not None
        assert updated.email_change_token is not None
    finally:
        await _delete_user(user.id)


async def test_request_email_update_wrong_password_returns_400(client) -> None:
    user = await _create_user(email=f"current_{uuid.uuid4().hex[:8]}@clinsights.dev")
    try:
        with patch("app.api.v1.endpoints.users.send_otp_email_task.delay") as mock_delay:
            response = await client.post(
                f"{API}/users/me/email",
                json={"email": f"new_{uuid.uuid4().hex[:8]}@clinsights.dev", "password": "wrong-password"},
                headers=_auth_headers(user.id),
            )

        assert response.status_code == 400
        assert response.json()["message"] == "Incorrect password"
        mock_delay.assert_not_called()
    finally:
        await _delete_user(user.id)


async def test_request_email_update_conflict_when_email_taken(client) -> None:
    user = await _create_user(email=f"current_{uuid.uuid4().hex[:8]}@clinsights.dev")
    existing = await _create_user(email=f"taken_{uuid.uuid4().hex[:8]}@clinsights.dev")
    try:
        with patch("app.api.v1.endpoints.users.send_otp_email_task.delay") as mock_delay:
            response = await client.post(
                f"{API}/users/me/email",
                json={"email": existing.email, "password": "Password123!"},
                headers=_auth_headers(user.id),
            )

        assert response.status_code == 409
        assert response.json()["message"] == "Email already in use"
        mock_delay.assert_not_called()
    finally:
        await _delete_user(user.id)
        await _delete_user(existing.id)


async def test_verify_email_update_valid_token_updates_email_and_clears_pending(client) -> None:
    user = await _create_user(email=f"current_{uuid.uuid4().hex[:8]}@clinsights.dev")
    new_email = f"new_{uuid.uuid4().hex[:8]}@clinsights.dev"
    try:
        with patch("app.api.v1.endpoints.users.send_otp_email_task.delay"):
            start_response = await client.post(
                f"{API}/users/me/email",
                json={"email": new_email, "password": "Password123!"},
                headers=_auth_headers(user.id),
            )
        assert start_response.status_code == 200

        updated = await _get_user(user.id)
        assert updated is not None
        assert updated.email_change_token is not None

        verify_response = await client.post(
            f"{API}/users/me/email/verify",
            json={"token": updated.email_change_token},
            headers=_auth_headers(user.id),
        )

        assert verify_response.status_code == 200
        verify_body = verify_response.json()
        assert verify_body["status"] == "success"
        assert verify_body["data"]["email"] == new_email

        final_user = await _get_user(user.id)
        assert final_user is not None
        assert final_user.email == new_email
        assert final_user.pending_email is None
        assert final_user.email_change_token is None
    finally:
        await _delete_user(user.id)


async def test_verify_email_update_invalid_token_returns_400(client) -> None:
    user = await _create_user(email=f"current_{uuid.uuid4().hex[:8]}@clinsights.dev")
    try:
        with patch("app.api.v1.endpoints.users.send_otp_email_task.delay"):
            start_response = await client.post(
                f"{API}/users/me/email",
                json={"email": f"new_{uuid.uuid4().hex[:8]}@clinsights.dev", "password": "Password123!"},
                headers=_auth_headers(user.id),
            )
        assert start_response.status_code == 200

        verify_response = await client.post(
            f"{API}/users/me/email/verify",
            json={"token": "000000"},
            headers=_auth_headers(user.id),
        )

        assert verify_response.status_code == 400
        assert verify_response.json()["message"] == "Invalid or expired token"
    finally:
        await _delete_user(user.id)


async def test_verify_email_update_expired_token_returns_400(client) -> None:
    user = await _create_user(email=f"current_{uuid.uuid4().hex[:8]}@clinsights.dev")
    try:
        with patch("app.api.v1.endpoints.users.send_otp_email_task.delay"):
            start_response = await client.post(
                f"{API}/users/me/email",
                json={"email": f"new_{uuid.uuid4().hex[:8]}@clinsights.dev", "password": "Password123!"},
                headers=_auth_headers(user.id),
            )
        assert start_response.status_code == 200

        otp = await _latest_otp_for_user(user.id)
        assert otp is not None

        async with AsyncSessionLocal() as session:
            otp_row = await session.get(OtpCode, otp.id)
            assert otp_row is not None
            otp_row.expires_at = datetime.now(timezone.utc) - timedelta(minutes=1)
            await session.commit()

        updated = await _get_user(user.id)
        assert updated is not None
        assert updated.email_change_token is not None

        verify_response = await client.post(
            f"{API}/users/me/email/verify",
            json={"token": updated.email_change_token},
            headers=_auth_headers(user.id),
        )

        assert verify_response.status_code == 400
        assert verify_response.json()["message"] == "Invalid or expired token"
    finally:
        await _delete_user(user.id)
