"""Unit tests for guest session service (Redis mocked in-process)."""

from __future__ import annotations

import uuid

import pytest

from app.services.guest import (
	GUEST_SESSION_KEY_PREFIX,
	create_guest_session,
	get_guest_session,
	normalize_guest_session_id,
	revoke_guest_session,
	touch_guest_session,
	validate_guest_session,
)
from tests.fakes.redis import FakeRedis


@pytest.fixture
def fake_redis() -> FakeRedis:
	return FakeRedis()


@pytest.mark.asyncio
async def test_create_guest_session_registers_key(fake_redis: FakeRedis) -> None:
	info = await create_guest_session(redis=fake_redis)

	assert uuid.UUID(info.guest_session_id)
	assert info.expires_in == 3600
	assert f"{GUEST_SESSION_KEY_PREFIX}{info.guest_session_id}" in fake_redis._data


@pytest.mark.asyncio
async def test_validate_guest_session_true_when_registered(fake_redis: FakeRedis) -> None:
	info = await create_guest_session(redis=fake_redis)

	assert await validate_guest_session(info.guest_session_id, redis=fake_redis) is True


@pytest.mark.asyncio
async def test_validate_guest_session_false_when_missing(fake_redis: FakeRedis) -> None:
	assert await validate_guest_session(str(uuid.uuid4()), redis=fake_redis) is False


@pytest.mark.asyncio
async def test_validate_guest_session_false_for_invalid_id(fake_redis: FakeRedis) -> None:
	assert await validate_guest_session("not-a-uuid", redis=fake_redis) is False
	assert await validate_guest_session("", redis=fake_redis) is False


@pytest.mark.asyncio
async def test_touch_guest_session_refreshes_existing(fake_redis: FakeRedis) -> None:
	info = await create_guest_session(redis=fake_redis)

	assert await touch_guest_session(info.guest_session_id, redis=fake_redis) is True


@pytest.mark.asyncio
async def test_touch_guest_session_false_when_missing(fake_redis: FakeRedis) -> None:
	assert await touch_guest_session(str(uuid.uuid4()), redis=fake_redis) is False


@pytest.mark.asyncio
async def test_revoke_guest_session_removes_key(fake_redis: FakeRedis) -> None:
	info = await create_guest_session(redis=fake_redis)

	assert await revoke_guest_session(info.guest_session_id, redis=fake_redis) is True
	assert await validate_guest_session(info.guest_session_id, redis=fake_redis) is False


@pytest.mark.asyncio
async def test_get_guest_session_returns_ttl(fake_redis: FakeRedis) -> None:
	info = await create_guest_session(redis=fake_redis)

	session = await get_guest_session(info.guest_session_id, redis=fake_redis)

	assert session is not None
	assert session.guest_session_id == info.guest_session_id
	assert session.expires_in == 3600


@pytest.mark.asyncio
async def test_get_guest_session_none_when_missing(fake_redis: FakeRedis) -> None:
	assert await get_guest_session(str(uuid.uuid4()), redis=fake_redis) is None


@pytest.mark.asyncio
async def test_normalize_guest_session_id_canonicalizes_uuid() -> None:
	raw = str(uuid.uuid4())
	upper = raw.upper()

	assert normalize_guest_session_id(upper) == str(uuid.UUID(raw))
