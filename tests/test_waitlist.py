"""API tests for the /waitlist endpoint: happy path, duplicates, rate limiting."""

from __future__ import annotations

import uuid
from unittest.mock import MagicMock, patch

import pytest
from httpx import AsyncClient

from app.core.config import get_settings

pytestmark = pytest.mark.asyncio(loop_scope="session")

API = "/api/v1/waitlist"

# Patch target: the task object in the module where .delay() is looked up
WAITLIST_EMAIL_TASK = "app.api.v1.endpoints.waitlist.send_waitlist_email_task"


def _unique_email() -> str:
	return f"waitlist_{uuid.uuid4().hex[:8]}@clinsights.dev"


async def test_join_waitlist_success(client: AsyncClient) -> None:
	"""A new email is added to the waitlist and first_name is persisted/returned."""
	email = _unique_email()

	with patch(WAITLIST_EMAIL_TASK) as mock_task:
		mock_task.delay = MagicMock()
		response = await client.post(API, json={"email": email, "first_name": "Ngozi"})

	assert response.status_code == 201
	body = response.json()
	assert body["status"] == "success"
	assert body["message"] == "You've been added to the waitlist!"
	assert body["data"]["email"] == email
	assert body["data"]["first_name"] == "Ngozi"
	assert "id" in body["data"]

	mock_task.delay.assert_called_once_with(to_email=email, first_name="Ngozi")


async def test_join_waitlist_without_first_name(client: AsyncClient) -> None:
	"""first_name is optional."""
	email = _unique_email()

	with patch(WAITLIST_EMAIL_TASK) as mock_task:
		mock_task.delay = MagicMock()
		response = await client.post(API, json={"email": email})

	assert response.status_code == 201
	body = response.json()
	assert body["data"]["first_name"] is None


async def test_join_waitlist_duplicate_email_conflicts(client: AsyncClient) -> None:
	"""Joining twice with the same email returns a 409 conflict, not a duplicate row."""
	email = _unique_email()

	with patch(WAITLIST_EMAIL_TASK) as mock_task:
		mock_task.delay = MagicMock()
		first = await client.post(API, json={"email": email, "first_name": "Ngozi"})
		second = await client.post(API, json={"email": email, "first_name": "Ngozi"})

	assert first.status_code == 201
	assert second.status_code == 409
	body = second.json()
	assert body["status"] == "error"
	assert body["message"] == "You're already on the waitlist."


async def test_join_waitlist_normalizes_email_case(client: AsyncClient) -> None:
	"""Email matching for duplicates is case-insensitive."""
	email = _unique_email()

	with patch(WAITLIST_EMAIL_TASK) as mock_task:
		mock_task.delay = MagicMock()
		first = await client.post(API, json={"email": email.lower(), "first_name": "Ngozi"})
		second = await client.post(API, json={"email": email.upper(), "first_name": "Ngozi"})

	assert first.status_code == 201
	assert second.status_code == 409


async def test_join_waitlist_validation_error(client: AsyncClient) -> None:
	"""An invalid email is rejected before hitting the DB."""
	response = await client.post(API, json={"email": "not-an-email"})
	assert response.status_code == 422


async def test_join_waitlist_rate_limited_after_repeated_requests(client: AsyncClient) -> None:
	"""A single IP is blocked with 429 after exceeding WAITLIST_RATE_LIMIT requests."""
	settings = get_settings()

	with patch(WAITLIST_EMAIL_TASK) as mock_task:
		mock_task.delay = MagicMock()
		for _ in range(settings.WAITLIST_RATE_LIMIT):
			resp = await client.post(API, json={"email": _unique_email(), "first_name": "Ngozi"})
			assert resp.status_code == 201

		blocked = await client.post(API, json={"email": _unique_email(), "first_name": "Ngozi"})

	assert blocked.status_code == 429
	body = blocked.json()
	assert body["status"] == "error"
	assert body["message"] == "Too many requests. Please try again later."
