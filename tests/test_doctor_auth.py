"""Doctor authentication endpoints."""

from __future__ import annotations

import uuid
from unittest.mock import patch

import pytest
from httpx import AsyncClient
from sqlalchemy import select

from app.core.security import hash_password, verify_password
from app.db.session import AsyncSessionLocal
from app.models.auth_session import AuthSession
from app.models.user import User, UserRole
from app.services.auth.tokens import create_access_token, create_refresh_token

pytestmark = pytest.mark.asyncio(loop_scope="session")

API = "/api/v1/auth/doctor"


def _doctor_signup_payload(**overrides: object) -> dict:
    base = {
        "first_name": "Dr",
        "last_name": "Smith",
        "email": f"doctor_{uuid.uuid4().hex[:8]}@clinsights.dev",
        "password": "Password123!",
        "confirm_password": "Password123!",
    }
    base.update(overrides)
    return base


async def _create_doctor_user(
    *,
    email: str,
    password: str = "Password123!",
    is_active: bool = True,
    is_email_verified: bool = True,
) -> User:
    user = User(
        id=uuid.uuid4(),
        email=email,
        first_name="Dr",
        last_name="Smith",
        role=UserRole.DOCTOR,
        is_active=is_active,
        is_email_verified=is_email_verified,
        password_hash=hash_password(password),
    )
    async with AsyncSessionLocal() as db:
        db.add(user)
        await db.commit()
        await db.refresh(user)
    return user


async def _delete_user(user_id: uuid.UUID) -> None:
    async with AsyncSessionLocal() as db:
        user = await db.get(User, user_id)
        if user is not None:
            await db.delete(user)
            await db.commit()


# ---------------------------------------------------------------------------
# Signup
# ---------------------------------------------------------------------------

async def test_doctor_signup_returns_201_and_dispatches_otp(client: AsyncClient) -> None:
    payload = _doctor_signup_payload()
    with patch("app.api.v1.endpoints.doctor_auth.send_otp_email_task.delay") as mock_delay:
        response = await client.post(f"{API}/signup", json=payload)

    assert response.status_code == 201, response.text
    body = response.json()
    assert body["status"] == "success"
    assert body["data"]["email"] == payload["email"]
    assert body["data"]["expires_in_seconds"] > 0
    assert mock_delay.called
    assert mock_delay.call_args.kwargs["to_email"] == payload["email"]
    assert mock_delay.call_args.kwargs["code"]
    assert len(mock_delay.call_args.kwargs["code"]) == 6
    assert mock_delay.call_args.kwargs["code"].isdigit()

    async with AsyncSessionLocal() as db:
        user = (await db.execute(select(User).where(User.email == payload["email"]))).scalar_one_or_none()
        assert user is not None
        assert user.role == UserRole.DOCTOR
        assert user.is_email_verified is False
        await _delete_user(user.id)


async def test_doctor_signup_rejects_duplicate_verified_email(client: AsyncClient) -> None:
    email = f"doctor_dup_{uuid.uuid4().hex[:8]}@clinsights.dev"
    await _create_doctor_user(email=email, is_email_verified=True)

    payload = _doctor_signup_payload(email=email)
    response = await client.post(f"{API}/signup", json=payload)
    assert response.status_code == 409, response.text

    await _delete_user_by_email(email)


async def test_doctor_signup_allows_duplicate_unverified_email(client: AsyncClient) -> None:
    email = f"doctor_unverified_{uuid.uuid4().hex[:8]}@clinsights.dev"
    await _create_doctor_user(email=email, is_email_verified=False)

    payload = _doctor_signup_payload(email=email)
    with patch("app.api.v1.endpoints.doctor_auth.send_otp_email_task.delay") as mock_delay:
        response = await client.post(f"{API}/signup", json=payload)

    assert response.status_code == 201, response.text
    assert mock_delay.called


async def test_doctor_signup_validates_password_strength(client: AsyncClient) -> None:
    payload = _doctor_signup_payload(password="weak", confirm_password="weak")
    response = await client.post(f"{API}/signup", json=payload)
    assert response.status_code == 422, response.text


async def test_doctor_signup_validates_name_length(client: AsyncClient) -> None:
    payload = _doctor_signup_payload(first_name="a" * 101)
    response = await client.post(f"{API}/signup", json=payload)
    assert response.status_code == 422, response.text


# ---------------------------------------------------------------------------
# Login
# ---------------------------------------------------------------------------

async def test_doctor_login_success_returns_tokens(client: AsyncClient) -> None:
    email = f"doctor_login_{uuid.uuid4().hex[:8]}@clinsights.dev"
    password = "Password123!"
    await _create_doctor_user(email=email, password=password, is_email_verified=True)

    response = await client.post(
        f"{API}/login",
        json={"email": email, "password": password, "device_id": "web-1", "platform": "web"},
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["status"] == "success"
    assert body["data"]["access_token"]
    assert body["data"]["refresh_token"]
    assert body["data"]["user"]["email"] == email
    assert body["data"]["user"]["role"] == "doctor"
    assert response.cookies.get("refresh_token") == body["data"]["refresh_token"]

    await _delete_user_by_email(email)


async def test_doctor_login_rejects_patient_account(client: AsyncClient) -> None:
    email = f"patient_block_{uuid.uuid4().hex[:8]}@clinsights.dev"
    password = "Password123!"
    user = User(
        id=uuid.uuid4(),
        email=email,
        first_name="Patient",
        last_name="User",
        role=UserRole.PATIENT,
        is_active=True,
        is_email_verified=True,
        password_hash=hash_password(password),
    )
    async with AsyncSessionLocal() as db:
        db.add(user)
        await db.commit()
        await db.refresh(user)

    response = await client.post(
        f"{API}/login",
        json={"email": email, "password": password, "device_id": "web-1", "platform": "web"},
    )
    assert response.status_code == 403, response.text
    body = response.json()
    assert body["status"] == "error"
    assert "does not have access" in body["message"].lower()

    await _delete_user_by_email(email)


async def test_patient_login_rejects_doctor_account(client: AsyncClient) -> None:
    email = f"doctor_block_{uuid.uuid4().hex[:8]}@clinsights.dev"
    password = "Password123!"
    await _create_doctor_user(email=email, password=password, is_email_verified=True)

    response = await client.post(
        "/api/v1/auth/login",
        json={"email": email, "password": password, "device_id": "web-1", "platform": "web"},
    )
    assert response.status_code == 403, response.text
    body = response.json()
    assert body["status"] == "error"
    assert "does not have access" in body["message"].lower()


async def test_doctor_login_rejects_unverified_account(client: AsyncClient) -> None:
    email = f"doctor_unverified_{uuid.uuid4().hex[:8]}@clinsights.dev"
    password = "Password123!"
    await _create_doctor_user(email=email, password=password, is_email_verified=False)

    response = await client.post(
        f"{API}/login",
        json={"email": email, "password": password, "device_id": "web-1", "platform": "web"},
    )
    assert response.status_code == 403, response.text
    body = response.json()
    assert "email not verified" in body["message"].lower()

    await _delete_user_by_email(email)


async def test_doctor_login_rejects_inactive_account(client: AsyncClient) -> None:
    email = f"doctor_inactive_{uuid.uuid4().hex[:8]}@clinsights.dev"
    password = "Password123!"
    await _create_doctor_user(email=email, password=password, is_active=False)

    response = await client.post(
        f"{API}/login",
        json={"email": email, "password": password, "device_id": "web-1", "platform": "web"},
    )
    assert response.status_code == 403, response.text
    body = response.json()
    assert "disabled" in body["message"].lower()

    await _delete_user_by_email(email)


async def test_doctor_login_rejects_invalid_credentials(client: AsyncClient) -> None:
    email = f"doctor_wrongpw_{uuid.uuid4().hex[:8]}@clinsights.dev"
    password = "Password123!"
    await _create_doctor_user(email=email, password=password, is_email_verified=True)

    response = await client.post(
        f"{API}/login",
        json={"email": email, "password": "WrongPassword!", "device_id": "web-1", "platform": "web"},
    )
    assert response.status_code == 401, response.text

    await _delete_user_by_email(email)


async def test_doctor_login_creates_auth_session_row(client: AsyncClient) -> None:
    email = f"doctor_sess_{uuid.uuid4().hex[:8]}@clinsights.dev"
    password = "Password123!"
    await _create_doctor_user(email=email, password=password, is_email_verified=True)

    login = await client.post(
        f"{API}/login",
        json={"email": email, "password": password, "device_id": "iphone-1", "platform": "ios"},
    )
    assert login.status_code == 200

    async with AsyncSessionLocal() as db:
        user = (await db.execute(select(User).where(User.email == email))).scalar_one()
        sessions = (
            await db.execute(
                select(AuthSession).where(AuthSession.user_id == user.id, AuthSession.revoked.is_(False))
            )
        ).scalars().all()
        assert len(sessions) == 1
        assert sessions[0].device_id == "ios:iphone-1"

    await _delete_user_by_email(email)


# ---------------------------------------------------------------------------
# Logout
# ---------------------------------------------------------------------------

async def test_doctor_logout_revokes_session(client: AsyncClient) -> None:
    email = f"doctor_logout_{uuid.uuid4().hex[:8]}@clinsights.dev"
    password = "Password123!"
    await _create_doctor_user(email=email, password=password, is_email_verified=True)

    login = await client.post(
        f"{API}/login",
        json={"email": email, "password": password, "device_id": "logout-dev"},
    )
    assert login.status_code == 200
    access = login.json()["data"]["access_token"]
    refresh = login.cookies.get("refresh_token")
    assert refresh

    logout = await client.post(
        f"{API}/logout",
        headers={"Authorization": f"Bearer {access}"},
        cookies={"refresh_token": refresh},
    )
    assert logout.status_code == 200, logout.text
    body = logout.json()
    assert body["status"] == "success"
    assert body["message"] == "Logged out successfully."

    async with AsyncSessionLocal() as db:
        user = (await db.execute(select(User).where(User.email == email))).scalar_one()
        sessions = (
            await db.execute(select(AuthSession).where(AuthSession.user_id == user.id))
        ).scalars().all()
        assert sessions
        assert all(s.revoked for s in sessions)

    await _delete_user_by_email(email)


async def test_doctor_logout_requires_refresh_token_cookie(client: AsyncClient) -> None:
	email = f"doctor_logout_cookie_{uuid.uuid4().hex[:8]}@clinsights.dev"
	password = "Password123!"
	await _create_doctor_user(email=email, password=password, is_email_verified=True)

	login = await client.post(
		f"{API}/login",
		json={"email": email, "password": password, "device_id": "logout-cookie-dev"},
	)
	assert login.status_code == 200
	access = login.json()["data"]["access_token"]

	client.cookies.clear()
	response = await client.post(
		f"{API}/logout",
		headers={"Authorization": f"Bearer {access}"},
	)
	assert response.status_code == 401, response.text

	await _delete_user_by_email(email)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

async def _delete_user_by_email(email: str) -> None:
    async with AsyncSessionLocal() as db:
        user = (await db.execute(select(User).where(User.email == email))).scalar_one_or_none()
        if user is not None:
            await db.delete(user)
            await db.commit()
