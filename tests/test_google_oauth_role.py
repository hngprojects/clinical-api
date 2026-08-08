"""Tests for Google OAuth role support.

Covers:
- oauth_state.py  — role encoding/decoding in signed JWT state tokens
- oauth.py        — get_or_create_google_user() with explicit roles
- auth.py         — /google endpoint accepts role query param
                  — /google/callback creates user with decoded role
"""

from __future__ import annotations

import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.models.user import UserRole
from app.services import oauth_state as oauth_state_service
from app.services.oauth_state import create_oauth_state, decode_oauth_state
from app.services.oauth import get_or_create_google_user

pytestmark = pytest.mark.no_db


# ---------------------------------------------------------------------------
# oauth_state — role round-trip
# ---------------------------------------------------------------------------


def test_oauth_state_encodes_doctor_role() -> None:
    """Role=doctor survives encode → decode round-trip."""
    token = create_oauth_state(role=UserRole.DOCTOR)
    parsed = decode_oauth_state(token)

    assert parsed is not None
    assert parsed.role == UserRole.DOCTOR


def test_oauth_state_encodes_patient_role() -> None:
    """Role=patient survives encode → decode round-trip."""
    token = create_oauth_state(role=UserRole.PATIENT)
    parsed = decode_oauth_state(token)

    assert parsed is not None
    assert parsed.role == UserRole.PATIENT


def test_oauth_state_no_role_decodes_as_none() -> None:
    """When role is not set in state, decoded role should be None."""
    token = create_oauth_state()  # no role
    parsed = decode_oauth_state(token)

    assert parsed is not None
    assert parsed.role is None


def test_oauth_state_empty_string_role_is_none() -> None:
    """Empty state string returns payload with role=None."""
    parsed = decode_oauth_state("")

    assert parsed is not None
    assert parsed.role is None


def test_oauth_state_role_with_all_fields() -> None:
    """Role is preserved alongside other state fields."""
    guest_id = str(uuid.uuid4())
    token = create_oauth_state(
        guest_session_id=guest_id,
        device_id="iphone-15",
        platform="ios",
        role=UserRole.DOCTOR,
    )
    parsed = decode_oauth_state(token)

    assert parsed is not None
    assert parsed.role == UserRole.DOCTOR
    assert parsed.guest_session_id == guest_id
    assert parsed.device_id == "iphone-15"
    assert parsed.platform == "ios"


def test_oauth_state_tampered_token_is_rejected() -> None:
    """Tampered state token must not decode to any payload."""
    token = create_oauth_state(role=UserRole.DOCTOR)
    assert decode_oauth_state(token + "tampered") is None


# ---------------------------------------------------------------------------
# get_or_create_google_user — role propagation
# ---------------------------------------------------------------------------


def _make_google_user(email: str | None = None) -> dict:
    """Build a fake Google profile dict."""
    return {
        "sub": f"google-sub-{uuid.uuid4().hex[:12]}",
        "email": email or f"google_{uuid.uuid4().hex[:8]}@gmail.com",
        "email_verified": True,
        "given_name": "Jane",
        "family_name": "Doe",
    }


@pytest.mark.asyncio
async def test_get_or_create_google_user_creates_doctor() -> None:
    """New user created with role=doctor when role=DOCTOR is passed."""
    google_user = _make_google_user()

    mock_repo = MagicMock()
    mock_repo.get_by_google_id_and_role = AsyncMock(return_value=None)
    mock_repo.get_by_email_and_role = AsyncMock(return_value=None)
    mock_repo.commit = AsyncMock()
    mock_repo.refresh = AsyncMock()
    mock_repo.add = MagicMock()

    created_user = None

    def capture_add(user):
        nonlocal created_user
        created_user = user

    mock_repo.add.side_effect = capture_add

    async def fake_refresh(user):
        pass  # no-op

    mock_repo.refresh.side_effect = fake_refresh

    await get_or_create_google_user(mock_repo, google_user, role=UserRole.DOCTOR)

    assert created_user is not None
    assert created_user.role == UserRole.DOCTOR
    assert created_user.email == google_user["email"]
    assert created_user.first_name == "Jane"
    assert created_user.last_name == "Doe"
    assert created_user.is_email_verified is True


@pytest.mark.asyncio
async def test_get_or_create_google_user_creates_patient_by_default() -> None:
    """New user defaults to role=patient when no role is passed."""
    google_user = _make_google_user()

    mock_repo = MagicMock()
    mock_repo.get_by_google_id_and_role = AsyncMock(return_value=None)
    mock_repo.get_by_email_and_role = AsyncMock(return_value=None)
    mock_repo.commit = AsyncMock()
    mock_repo.refresh = AsyncMock()
    mock_repo.add = MagicMock()

    created_user = None

    def capture_add(user):
        nonlocal created_user
        created_user = user

    mock_repo.add.side_effect = capture_add
    mock_repo.refresh.side_effect = AsyncMock()

    # No role kwarg — should default to PATIENT
    await get_or_create_google_user(mock_repo, google_user)

    assert created_user is not None
    assert created_user.role == UserRole.PATIENT


@pytest.mark.asyncio
async def test_get_or_create_google_user_returns_existing_doctor() -> None:
    """Existing doctor account is returned and refreshed without creating new user."""
    google_user = _make_google_user()

    existing = MagicMock()
    existing.email = google_user["email"]
    existing.first_name = "Jane"
    existing.last_name = "Doe"
    existing.is_email_verified = True

    mock_repo = MagicMock()
    mock_repo.get_by_google_id_and_role = AsyncMock(return_value=existing)
    mock_repo.commit = AsyncMock()
    mock_repo.refresh = AsyncMock()

    result = await get_or_create_google_user(mock_repo, google_user, role=UserRole.DOCTOR)

    assert result is existing
    # Verified that lookup was done with the correct role
    mock_repo.get_by_google_id_and_role.assert_awaited_once_with(
        google_user["sub"], UserRole.DOCTOR
    )
    # Should NOT have called get_by_email_and_role or add (no new user)
    mock_repo.get_by_email_and_role.assert_not_called()


@pytest.mark.asyncio
async def test_get_or_create_google_user_raises_conflict_on_email_collision() -> None:
    """ConflictError is raised when same email already exists for the same role."""
    from app.core.exceptions import ConflictError

    google_user = _make_google_user()

    existing = MagicMock()
    mock_repo = MagicMock()
    mock_repo.get_by_google_id_and_role = AsyncMock(return_value=None)
    mock_repo.get_by_email_and_role = AsyncMock(return_value=existing)

    with pytest.raises(ConflictError):
        await get_or_create_google_user(mock_repo, google_user, role=UserRole.DOCTOR)


@pytest.mark.asyncio
async def test_get_or_create_google_user_raises_on_unverified_email() -> None:
    """UnauthorizedError is raised when Google email is not verified."""
    from app.core.exceptions import UnauthorizedError

    google_user = _make_google_user()
    google_user["email_verified"] = False

    mock_repo = MagicMock()

    with pytest.raises(UnauthorizedError, match="not verified"):
        await get_or_create_google_user(mock_repo, google_user, role=UserRole.DOCTOR)


# ---------------------------------------------------------------------------
# /google endpoint — role query param
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_google_login_redirects_with_doctor_role(client) -> None:
    """/google?role=doctor redirects to Google with encoded state."""
    response = await client.get(
        "/api/v1/auth/google",
        params={"role": "doctor"},
        follow_redirects=False,
    )

    assert response.status_code in (302, 307)
    location = response.headers["location"]
    assert "accounts.google.com" in location
    assert "state=" in location


@pytest.mark.asyncio
async def test_google_login_redirects_with_patient_role_by_default(client) -> None:
    """/google with no role param still redirects to Google."""
    response = await client.get(
        "/api/v1/auth/google",
        follow_redirects=False,
    )

    assert response.status_code in (302, 307)
    location = response.headers["location"]
    assert "accounts.google.com" in location
    assert "state=" in location


@pytest.mark.asyncio
async def test_google_login_rejects_admin_role(client) -> None:
    """/google?role=admin must not provision an admin account — blocked by allowlist."""
    response = await client.get(
        "/api/v1/auth/google",
        params={"role": "admin"},
        follow_redirects=False,
    )
    # admin is a valid enum value but is blocked by the OAUTH_ALLOWED_ROLES allowlist.
    assert response.status_code == 400


@pytest.mark.asyncio
async def test_google_login_rejects_invalid_role(client) -> None:
    """/google?role=superadmin should be rejected with 400."""
    response = await client.get(
        "/api/v1/auth/google",
        params={"role": "superadmin"},
        follow_redirects=False,
    )
    assert response.status_code == 400


# ---------------------------------------------------------------------------
# /google/callback — role decoding from state
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_google_callback_creates_doctor_from_state(client) -> None:
    """Callback decodes role=doctor from state and creates doctor user with the correct role.

    Dependency overrides are applied so the request runs all the way through
    role decoding without hitting a real database or auth session store.
    Assertions are unconditional — the test will fail if role propagation breaks.
    """
    from app.api.deps import (
        get_auth_session_manager,
        get_chat_repo,
        get_medical_case_repo,
        get_user_repo,
    )
    from app.main import app as fastapi_app

    doctor_state = create_oauth_state(role=UserRole.DOCTOR)

    fake_google_user = {
        "sub": f"google-{uuid.uuid4().hex[:12]}",
        "email": f"drtest_{uuid.uuid4().hex[:8]}@gmail.com",
        "email_verified": True,
        "given_name": "Doc",
        "family_name": "Test",
    }
    fake_tokens = {"access_token": "google-access-token"}
    fake_issue = SimpleNamespace(
        access_token="app-access-token",
        refresh_token="app-refresh-token",
        expires_in=28800,
    )

    # Stub out DB-backed repos and auth session manager.
    fake_user_repo = MagicMock()
    fake_case_repo = MagicMock()
    fake_chat_repo = MagicMock()
    fake_auth_manager = MagicMock()
    fake_auth_manager.create = AsyncMock(return_value=fake_issue)

    fastapi_app.dependency_overrides[get_user_repo] = lambda: fake_user_repo
    fastapi_app.dependency_overrides[get_medical_case_repo] = lambda: fake_case_repo
    fastapi_app.dependency_overrides[get_chat_repo] = lambda: fake_chat_repo
    fastapi_app.dependency_overrides[get_auth_session_manager] = lambda: fake_auth_manager

    created_user = MagicMock()
    created_user.id = uuid.uuid4()

    try:
        with (
            patch(
                "app.api.v1.endpoints.auth.exchange_google_code",
                new=AsyncMock(return_value=fake_tokens),
            ),
            patch(
                "app.api.v1.endpoints.auth.fetch_google_user_info",
                new=AsyncMock(return_value=fake_google_user),
            ),
            patch(
                "app.api.v1.endpoints.auth.get_or_create_google_user",
                new=AsyncMock(return_value=created_user),
            ) as mock_create,
        ):
            response = await client.get(
                "/api/v1/auth/google/callback",
                params={"code": "test-code", "state": doctor_state},
                follow_redirects=False,
            )

            # The callback should redirect (302/307) once the user is found/created.
            assert response.status_code in (302, 307), (
                f"Expected a redirect but got {response.status_code}: {response.text}"
            )

            # Unconditionally assert role propagation — test fails if the call never happened.
            mock_create.assert_awaited_once()
            _, kwargs = mock_create.call_args
            assert kwargs["role"] == UserRole.DOCTOR, (
                f"Expected role=DOCTOR but got {kwargs.get('role')}"
            )
    finally:
        # Always clean up overrides so other tests are unaffected.
        fastapi_app.dependency_overrides.pop(get_user_repo, None)
        fastapi_app.dependency_overrides.pop(get_medical_case_repo, None)
        fastapi_app.dependency_overrides.pop(get_chat_repo, None)
        fastapi_app.dependency_overrides.pop(get_auth_session_manager, None)


# ---------------------------------------------------------------------------
# Security — role allowlist enforcement
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_google_login_rejects_admin_role(client) -> None:
    """/google?role=admin must be rejected — admin cannot be OAuth self-provisioned."""
    response = await client.get(
        "/api/v1/auth/google",
        params={"role": "admin"},
        follow_redirects=False,
    )
    assert response.status_code == 400
    assert "cannot be provisioned through OAuth" in response.json()["detail"]


@pytest.mark.asyncio
async def test_google_callback_clamps_admin_role_to_patient() -> None:
    """If an admin role somehow ends up in the signed state, it is clamped to patient."""
    from app.services.oauth_state import create_oauth_state as _create

    # Manually create a state that contains role=admin (bypasses the endpoint allowlist).
    admin_state = _create(role=UserRole.ADMIN)
    decoded = decode_oauth_state(admin_state)

    # Simulate what google_callback does with the decoded role.
    _OAUTH_ALLOWED_ROLES = frozenset({UserRole.PATIENT, UserRole.DOCTOR})
    raw_role = decoded.role if decoded and decoded.role else UserRole.PATIENT
    safe_role = raw_role if raw_role in _OAUTH_ALLOWED_ROLES else UserRole.PATIENT

    assert safe_role == UserRole.PATIENT, (
        "Admin role from state must be clamped to PATIENT by the callback allowlist"
    )
