"""Unit tests for guest session service (Postgres via GuestSessionManager)."""

from __future__ import annotations

import uuid

import pytest

from app.db.session import AsyncSessionLocal
from app.repositories.guest_session import GuestSessionRepository
from app.services.guest import (
	create_guest_session,
	get_guest_session,
	normalize_guest_session_id,
	revoke_guest_session,
	touch_guest_session,
	validate_guest_session,
)
from app.services.guest_sessions import GuestSessionManager

pytestmark = pytest.mark.asyncio(loop_scope="session")


async def test_create_guest_session_persists_row() -> None:
	info = await create_guest_session()

	assert uuid.UUID(info.guest_session_id)
	assert 3590 <= info.expires_in <= 3600

	async with AsyncSessionLocal() as db:
		row = await GuestSessionRepository(db).get_by_id(uuid.UUID(info.guest_session_id))
	assert row is not None
	assert row.revoked is False


async def test_validate_guest_session_true_when_active() -> None:
	info = await create_guest_session()

	assert await validate_guest_session(info.guest_session_id) is True


async def test_validate_guest_session_false_when_missing() -> None:
	assert await validate_guest_session(str(uuid.uuid4())) is False


async def test_validate_guest_session_false_for_invalid_id() -> None:
	assert await validate_guest_session("not-a-uuid") is False
	assert await validate_guest_session("") is False


async def test_touch_guest_session_refreshes_existing() -> None:
	info = await create_guest_session()

	assert await touch_guest_session(info.guest_session_id) is True


async def test_touch_guest_session_false_when_missing() -> None:
	assert await touch_guest_session(str(uuid.uuid4())) is False


async def test_revoke_guest_session_marks_row_revoked() -> None:
	info = await create_guest_session()

	assert await revoke_guest_session(info.guest_session_id) is True
	assert await validate_guest_session(info.guest_session_id) is False


async def test_get_guest_session_returns_ttl() -> None:
	info = await create_guest_session()

	session = await get_guest_session(info.guest_session_id)

	assert session is not None
	assert session.guest_session_id == info.guest_session_id
	assert 3590 <= session.expires_in <= 3600


async def test_get_guest_session_none_when_missing() -> None:
	assert await get_guest_session(str(uuid.uuid4())) is None


async def test_normalize_guest_session_id_canonicalizes_uuid() -> None:
	raw = str(uuid.uuid4())
	upper = raw.upper()

	assert normalize_guest_session_id(upper) == str(uuid.UUID(raw))


async def test_manager_revoke_marks_row() -> None:
	async with AsyncSessionLocal() as db:
		manager = GuestSessionManager(GuestSessionRepository(db))
		session = await manager.create("ip-a", f"fp-{uuid.uuid4()}")
		await manager.revoke(session.id)
		row = await GuestSessionRepository(db).get_by_id(session.id)
	assert row is not None
	assert row.revoked is True
