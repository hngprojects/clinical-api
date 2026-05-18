"""API tests for guest session endpoints."""

from __future__ import annotations

import uuid

import pytest
from httpx import AsyncClient

from app.services.guest import create_guest_session, revoke_guest_session
from tests.fakes.redis import FakeRedis

pytestmark = pytest.mark.asyncio(loop_scope="session")

API = "/api/v1/guest/sessions"
GUEST_HEADER = "X-Guest-Session-Id"


@pytest.fixture
def fake_redis() -> FakeRedis:
	return FakeRedis()


@pytest.fixture(autouse=True)
def patch_redis(fake_redis: FakeRedis, monkeypatch: pytest.MonkeyPatch) -> None:
	async def _get_redis() -> FakeRedis:
		return fake_redis

	monkeypatch.setattr("app.core.redis_client.get_redis", _get_redis)
	monkeypatch.setattr("app.services.guest.get_redis", _get_redis)


async def test_create_guest_session_returns_201(client: AsyncClient) -> None:
	response = await client.post(API)

	assert response.status_code == 201
	body = response.json()
	assert body["status"] == "success"
	assert body["message"] == "Guest session created."
	data = body["data"]
	assert uuid.UUID(data["guest_session_id"])
	assert 3590 <= data["expires_in"] <= 3600


async def test_session_me_valid_header_returns_200(client: AsyncClient, fake_redis: FakeRedis) -> None:
	info = await create_guest_session(redis=fake_redis)

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


async def test_session_me_expired_session_returns_401(client: AsyncClient, fake_redis: FakeRedis) -> None:
	info = await create_guest_session(redis=fake_redis)
	await revoke_guest_session(info.guest_session_id, redis=fake_redis)

	response = await client.get(f"{API}/me", headers={GUEST_HEADER: info.guest_session_id})

	assert response.status_code == 401
	assert "expired" in response.json()["message"].lower()
