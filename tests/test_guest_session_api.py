"""API tests for guest session endpoints."""

from __future__ import annotations

import uuid

import pytest
from httpx import AsyncClient

from app.services.guest import create_guest_session, revoke_guest_session

pytestmark = pytest.mark.asyncio(loop_scope="session")

API = "/api/v1/guest-session"
GUEST_HEADER = "X-Guest-Session-Id"


async def test_create_guest_session_returns_201(client: AsyncClient) -> None:
	response = await client.post(
		API,
		headers={"X-Device-Fingerprint": f"api-{uuid.uuid4().hex[:8]}"},
	)

	assert response.status_code == 201
	body = response.json()
	assert body["status"] == "success"
	data = body["data"]
	assert uuid.UUID(data["guest_session_id"])
	assert 3590 <= data["expires_in"] <= 3600


async def test_session_me_valid_header_returns_200(client: AsyncClient) -> None:
	info = await create_guest_session()

	response = await client.get(f"{API}/me", headers={GUEST_HEADER: info.guest_session_id})

	assert response.status_code == 200
	data = response.json()["data"]
	assert data["guest_session_id"] == info.guest_session_id
	assert 3590 <= data["expires_in"] <= 3600


async def test_session_me_missing_header_returns_401(client: AsyncClient) -> None:
	response = await client.get(f"{API}/me")

	assert response.status_code == 401
	assert "Missing" in response.json()["message"]


async def test_session_me_invalid_header_returns_401(client: AsyncClient) -> None:
	response = await client.get(f"{API}/me", headers={GUEST_HEADER: "not-a-valid-uuid"})

	assert response.status_code == 401


async def test_session_me_expired_session_returns_401(client: AsyncClient) -> None:
	info = await create_guest_session()
	await revoke_guest_session(info.guest_session_id)

	response = await client.get(f"{API}/me", headers={GUEST_HEADER: info.guest_session_id})

	assert response.status_code == 401
	assert "expired" in response.json()["message"].lower()
