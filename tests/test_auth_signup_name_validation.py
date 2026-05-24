"""Signup first/last name validation messages (BUG-009–012)."""

from __future__ import annotations

import uuid

import pytest
from httpx import AsyncClient
from pydantic import ValidationError

from app.schemas.auth import SignupRequest

API = "/api/v1/auth"
SIGNUP = f"{API}/signup"


def _signup_payload(**overrides: object) -> dict:
	base = {
		"first_name": "Test",
		"last_name": "User",
		"email": f"signup_{uuid.uuid4().hex[:8]}@clinsights.dev",
		"password": "Password123!",
		"confirm_password": "Password123!",
	}
	base.update(overrides)
	return base


def test_signup_first_name_too_long_raises_clear_message() -> None:
	with pytest.raises(ValidationError) as exc_info:
		SignupRequest(**_signup_payload(first_name="a" * 101))

	assert "First name must be 100 characters or fewer." in str(exc_info.value)


def test_signup_last_name_too_long_raises_clear_message() -> None:
	with pytest.raises(ValidationError):
		SignupRequest(**_signup_payload(last_name="b" * 101))


def test_signup_first_name_whitespace_raises_clear_message() -> None:
	with pytest.raises(ValidationError) as exc_info:
		SignupRequest(**_signup_payload(first_name="   "))

	assert exc_info.value.errors()[0]["msg"] == "Value error, First name cannot be empty."


def test_signup_last_name_whitespace_raises_clear_message() -> None:
	with pytest.raises(ValidationError) as exc_info:
		SignupRequest(**_signup_payload(last_name="   "))

	assert exc_info.value.errors()[0]["msg"] == "Value error, Last name cannot be empty."


@pytest.mark.no_db
@pytest.mark.asyncio(loop_scope="session")
async def test_signup_validation_http_message(client: AsyncClient) -> None:
	response = await client.post(SIGNUP, json=_signup_payload(first_name="   "))

	assert response.status_code == 422
	assert response.json()["message"] == "First name cannot be empty."
