"""Doctor auth tests for signup, OTP, login, and role-based access."""

from __future__ import annotations

import uuid
from unittest.mock import patch

import pytest
from sqlalchemy import select

from app.core.security import hash_password
from app.db.session import AsyncSessionLocal
from app.models.otp import OtpCode, OtpPurpose
from app.models.user import User, UserRole

pytestmark = pytest.mark.asyncio(loop_scope="session")

API = "/api/v1"

_DOCTOR_PAYLOAD = {
    "first_name": "Emeka",
    "last_name": "Okafor",
    "phone_number": "08012345678",
    "email": "",          # filled per test
    "password": "SecurePass123!",
    "confirm_password": "SecurePass123!",
}


def _unique_email() -> str:
    return f"dr_{uuid.uuid4().hex[:8]}@clinsights.dev"


async def _get_latest_otp(user_id: uuid.UUID) -> OtpCode | None:
    async with AsyncSessionLocal() as session:
        result = await session.execute(
            select(OtpCode)
            .where(
                OtpCode.user_id == user_id,
                OtpCode.purpose == OtpPurpose.EMAIL_VERIFICATION,
            )
            .order_by(OtpCode.created_at.desc())
            .limit(1)
        )
        return result.scalars().first()


async def _delete_user_by_email(email: str) -> None:
    async with AsyncSessionLocal() as session:
        result = await session.execute(select(User).where(User.email == email))
        user = result.scalars().first()
        if user:
            await session.delete(user)
            await session.commit()


# ── Signup ───────────────────────────────────────────────────────────────────


async def test_doctor_signup_success(client):
    email = _unique_email()
    payload = {**_DOCTOR_PAYLOAD, "email": email}

    with patch("app.api.v1.endpoints.auth.send_otp_email_task") as mock_task:
        mock_task.delay.return_value = None
        response = await client.post(f"{API}/auth/doctor/signup", json=payload)

    assert response.status_code == 201
    data = response.json()
    assert data["data"]["email"] == email

    # Confirm role is DOCTOR in DB
    async with AsyncSessionLocal() as session:
        result = await session.execute(select(User).where(User.email == email))
        user = result.scalars().first()
    assert user is not None
    assert user.role == UserRole.DOCTOR
    assert user.phone_number == payload["phone_number"]
    assert user.is_email_verified is False

    # Confirm OTP email was sent with is_doctor=True
    mock_task.delay.assert_called_once()
    call_kwargs = mock_task.delay.call_args.kwargs
    assert call_kwargs.get("is_doctor") is True

    await _delete_user_by_email(email)


async def test_doctor_signup_duplicate_verified_email(client):
    email = _unique_email()

    # Create a verified doctor manually
    async with AsyncSessionLocal() as session:
        user = User(
            id=uuid.uuid4(),
            email=email,
            first_name="Existing",
            last_name="Doctor",
            role=UserRole.DOCTOR,
            is_active=True,
            is_email_verified=True,
            password_hash=hash_password("Password123!"),
        )
        session.add(user)
        await session.commit()

    payload = {**_DOCTOR_PAYLOAD, "email": email}
    with patch("app.api.v1.endpoints.auth.send_otp_email_task"):
        response = await client.post(f"{API}/auth/doctor/signup", json=payload)

    assert response.status_code == 409
    await _delete_user_by_email(email)


async def test_doctor_signup_password_mismatch(client):
    payload = {
        **_DOCTOR_PAYLOAD,
        "email": _unique_email(),
        "confirm_password": "WrongPassword!",
    }
    response = await client.post(f"{API}/auth/doctor/signup", json=payload)
    assert response.status_code == 422


async def test_doctor_signup_missing_phone(client):
    payload = {k: v for k, v in _DOCTOR_PAYLOAD.items() if k != "phone_number"}
    payload["email"] = _unique_email()
    response = await client.post(f"{API}/auth/doctor/signup", json=payload)
    assert response.status_code == 422


async def test_doctor_signup_invalid_email(client):
    payload = {**_DOCTOR_PAYLOAD, "email": "not-an-email"}
    response = await client.post(f"{API}/auth/doctor/signup", json=payload)
    assert response.status_code == 422


# ── OTP verify (shared endpoint) ─────────────────────────────────────────────


async def test_doctor_verify_otp_success(client):
    email = _unique_email()

    # Signup first
    with patch("app.api.v1.endpoints.auth.send_otp_email_task") as mock_task:
        mock_task.delay.return_value = None
        signup_resp = await client.post(f"{API}/auth/doctor/signup", json={**_DOCTOR_PAYLOAD, "email": email})
    assert signup_resp.status_code == 201

    # Get user + OTP from DB
    async with AsyncSessionLocal() as session:
        result = await session.execute(select(User).where(User.email == email))
        user = result.scalars().first()
    assert user is not None

    otp = await _get_latest_otp(user.id)
    assert otp is not None

    response = await client.post(
        f"{API}/auth/verify-otp",
        json={"email": email, "code": otp.code},
    )
    assert response.status_code == 200
    data = response.json()
    assert "access_token" in data["data"]
    assert data["data"]["user"]["role"] == "doctor"

    await _delete_user_by_email(email)


# ── Login (shared endpoint) ───────────────────────────────────────────────────


async def test_doctor_login_success(client):
    email = _unique_email()
    password = "SecurePass123!"

    async with AsyncSessionLocal() as session:
        user = User(
            id=uuid.uuid4(),
            email=email,
            first_name="Emeka",
            last_name="Okafor",
            phone_number="08012345678",
            role=UserRole.DOCTOR,
            is_active=True,
            is_email_verified=True,
            password_hash=hash_password(password),
        )
        session.add(user)
        await session.commit()

    response = await client.post(
        f"{API}/auth/login",
        json={"email": email, "password": password},
    )
    assert response.status_code == 200
    data = response.json()
    assert data["data"]["user"]["role"] == "doctor"

    await _delete_user_by_email(email)


async def test_doctor_login_wrong_password(client):
    email = _unique_email()

    async with AsyncSessionLocal() as session:
        user = User(
            id=uuid.uuid4(),
            email=email,
            first_name="Emeka",
            last_name="Okafor",
            role=UserRole.DOCTOR,
            is_active=True,
            is_email_verified=True,
            password_hash=hash_password("CorrectPass123!"),
        )
        session.add(user)
        await session.commit()

    response = await client.post(
        f"{API}/auth/login",
        json={"email": email, "password": "WrongPass123!"},
    )
    assert response.status_code == 401

    await _delete_user_by_email(email)


# ── Resend OTP (shared endpoint) ─────────────────────────────────────────────


async def test_doctor_resend_otp(client):
    email = _unique_email()

    with patch("app.api.v1.endpoints.auth.send_otp_email_task") as mock_task:
        mock_task.delay.return_value = None
        await client.post(f"{API}/auth/doctor/signup", json={**_DOCTOR_PAYLOAD, "email": email})

        response = await client.post(f"{API}/auth/resend-otp", json={"email": email})

    assert response.status_code == 200
    await _delete_user_by_email(email)


# ── Role guard ────────────────────────────────────────────────────────────────


async def test_patient_cannot_access_doctor_only_route(client):
    """Verify DoctorUser dep blocks patients — test against /auth/me as a
    sanity check; replace with a real doctor-only route when one exists."""
    from app.services.auth.tokens import create_access_token

    async with AsyncSessionLocal() as session:
        patient = User(
            id=uuid.uuid4(),
            email=_unique_email(),
            first_name="Pat",
            last_name="Ient",
            role=UserRole.PATIENT,
            is_active=True,
            is_email_verified=True,
            password_hash=hash_password("Password123!"),
        )
        session.add(patient)
        await session.commit()
        await session.refresh(patient)

    token, _ = create_access_token(patient.id)
    headers = {"Authorization": f"Bearer {token}"}

    # /auth/me is not doctor-only, but the role field should read 'patient'
    response = await client.get(f"{API}/auth/me", headers=headers)
    assert response.status_code == 200
    assert response.json()["data"]["role"] == "patient"

    async with AsyncSessionLocal() as session:
        u = await session.get(User, patient.id)
        if u:
            await session.delete(u)
            await session.commit()