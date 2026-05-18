"""Guest session helpers: Redis cache + GuestSessionManager (Postgres)."""

from __future__ import annotations

import json
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from uuid import UUID

import redis.asyncio as aioredis

from app.core.config import get_settings
from app.core.exceptions import UnauthorizedError
from app.core.redis_client import get_redis
from app.db.session import AsyncSessionLocal
from app.models.guest_session import GuestSession
from app.repositories.chat import ChatRepository
from app.repositories.guest_session import GuestSessionRepository
from app.repositories.medical_case import MedicalCaseRepository
from app.services.guest_sessions import GuestSessionManager

GUEST_SESSION_KEY_PREFIX = "guest:session:"


@dataclass(frozen=True)
class GuestSessionInfo:
	"""Issued guest session metadata returned to clients."""

	guest_session_id: str
	expires_in: int


@dataclass(frozen=True)
class GuestMigrationResult:
	"""Counts from linking guest-owned rows to a user account."""

	cases_migrated: int
	chats_updated: int


def _session_key(session_id: str) -> str:
	return f"{GUEST_SESSION_KEY_PREFIX}{session_id}"


def _expires_in(session: GuestSession) -> int:
	now = datetime.now(timezone.utc)
	return max(0, int((session.expires_at - now).total_seconds()))


def _session_info(session: GuestSession) -> GuestSessionInfo:
	return GuestSessionInfo(guest_session_id=str(session.id), expires_in=_expires_in(session))


def _manager(db) -> GuestSessionManager:
	return GuestSessionManager(GuestSessionRepository(db))


def to_guest_session_uuid(session_id: str | UUID) -> UUID:
	"""Parse a header/body token into a UUID (raises ValueError if invalid)."""
	if isinstance(session_id, UUID):
		return session_id
	normalized = normalize_guest_session_id(str(session_id))
	if normalized is None:
		raise ValueError("Invalid guest session id.")
	return UUID(normalized)


async def _sync_redis_cache(
	session_id: str,
	*,
	redis: aioredis.Redis | None = None,
	ttl: int | None = None,
) -> None:
	client = redis if redis is not None else await get_redis()
	settings = get_settings()
	ex = ttl if ttl is not None else settings.GUEST_SESSION_TTL_SECONDS
	payload = json.dumps({"cached_at": datetime.now(timezone.utc).isoformat()})
	await client.set(_session_key(session_id), payload, ex=ex)


async def resolve_guest_session_id(
	session_id: str | UUID | None,
	*,
	redis: aioredis.Redis | None = None,
) -> UUID:
	"""Validate guest session in Postgres and return the canonical session UUID."""
	if session_id is None or (isinstance(session_id, str) and not session_id.strip()):
		raise UnauthorizedError("Missing guest session. Provide X-Guest-Session-Id or guest_session_id.")
	try:
		session_uuid = to_guest_session_uuid(session_id)
	except ValueError as exc:
		raise UnauthorizedError("Invalid guest session id.") from exc

	async with AsyncSessionLocal() as db:
		session = await _manager(db).get(session_uuid)
	if session is None:
		raise UnauthorizedError("Guest session expired or invalid.")

	await _sync_redis_cache(str(session_uuid), redis=redis, ttl=_expires_in(session))
	return session_uuid


def normalize_guest_session_id(session_id: str | UUID | None) -> str | None:
	"""Return canonical UUID string or None if the value is not a valid UUID."""
	if session_id is None:
		return None
	if isinstance(session_id, UUID):
		return str(session_id)
	if not session_id or not session_id.strip():
		return None
	try:
		return str(uuid.UUID(session_id.strip()))
	except ValueError:
		return None


async def create_guest_session(
	*,
	redis: aioredis.Redis | None = None,
	ip_hash: str = "test-ip-hash",
	device_fingerprint: str | None = None,
) -> GuestSessionInfo:
	"""Issue or reuse a guest session (Postgres source of truth, Redis cache)."""
	fp = device_fingerprint if device_fingerprint is not None else f"test-{uuid.uuid4()}"
	async with AsyncSessionLocal() as db:
		session = await _manager(db).create(ip_hash, fp)
	info = _session_info(session)
	await _sync_redis_cache(info.guest_session_id, redis=redis, ttl=info.expires_in)
	return info


async def get_guest_session(
	session_id: str,
	*,
	redis: aioredis.Redis | None = None,
) -> GuestSessionInfo | None:
	"""Return session metadata when the id is valid and still active."""
	normalized = normalize_guest_session_id(session_id)
	if normalized is None:
		return None
	async with AsyncSessionLocal() as db:
		session = await _manager(db).get(UUID(normalized))
	if session is None:
		return None
	info = _session_info(session)
	await _sync_redis_cache(info.guest_session_id, redis=redis, ttl=info.expires_in)
	return info


async def validate_guest_session(
	session_id: str,
	*,
	redis: aioredis.Redis | None = None,
) -> bool:
	"""Return True when the session id exists and is active in Postgres."""
	normalized = normalize_guest_session_id(session_id)
	if normalized is None:
		return False
	async with AsyncSessionLocal() as db:
		session = await _manager(db).get(UUID(normalized))
	if session is None:
		return False
	await _sync_redis_cache(normalized, redis=redis, ttl=_expires_in(session))
	return True


async def touch_guest_session(
	session_id: str | UUID,
	*,
	redis: aioredis.Redis | None = None,
) -> bool:
	"""Refresh session expiry on activity. Returns False if the session is missing or invalid."""
	normalized = normalize_guest_session_id(session_id)
	if normalized is None:
		return False
	async with AsyncSessionLocal() as db:
		session = await _manager(db).touch(UUID(normalized))
	if session is None:
		return False
	info = _session_info(session)
	await _sync_redis_cache(info.guest_session_id, redis=redis, ttl=info.expires_in)
	return True


async def revoke_guest_session(
	session_id: str,
	*,
	redis: aioredis.Redis | None = None,
) -> bool:
	"""Revoke a guest session in Postgres and remove its Redis cache."""
	normalized = normalize_guest_session_id(session_id)
	if normalized is None:
		return False
	async with AsyncSessionLocal() as db:
		await _manager(db).revoke(UUID(normalized))
	client = redis if redis is not None else await get_redis()
	deleted = await client.delete(_session_key(normalized))
	return deleted > 0


async def migrate_guest_session_to_user(
	case_repo: MedicalCaseRepository,
	chat_repo: ChatRepository,
	*,
	guest_session_id: str | UUID,
	user_id: UUID,
	redis: aioredis.Redis | None = None,
) -> GuestMigrationResult:
	"""Attach guest cases and orphan chats to the authenticated user; revoke guest session."""
	try:
		session_uuid = to_guest_session_uuid(guest_session_id)
	except ValueError:
		return GuestMigrationResult(cases_migrated=0, chats_updated=0)

	cases = await case_repo.get_by_guest_session(session_uuid, offset=0, limit=500)
	guest_row = await case_repo._session.get(GuestSession, session_uuid)
	if guest_row is not None:
		guest_row.migrated_user_id = user_id
		guest_row.revoked = True

	if not cases:
		await case_repo.commit()
		await revoke_guest_session(str(session_uuid), redis=redis)
		return GuestMigrationResult(cases_migrated=0, chats_updated=0)

	case_ids: list[UUID] = []
	for case in cases:
		case.user_id = user_id
		case.guest_session_id = None
		case_ids.append(case.id)

	chats_updated = await chat_repo.assign_user_to_case_messages(case_ids, user_id)
	await case_repo.commit()
	await revoke_guest_session(str(session_uuid), redis=redis)
	return GuestMigrationResult(cases_migrated=len(cases), chats_updated=chats_updated)
