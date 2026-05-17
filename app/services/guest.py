"""Guest session lifecycle backed by Redis TTL keys."""

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
from app.repositories.chat import ChatRepository
from app.repositories.medical_case import MedicalCaseRepository

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


async def resolve_guest_session_id(
	session_id: str | None,
	*,
	redis: aioredis.Redis | None = None,
) -> str:
	"""Validate guest session in Redis and return a canonical UUID string."""
	if not session_id or not session_id.strip():
		raise UnauthorizedError("Missing guest session. Provide X-Guest-Session-Id or guest_session_id.")
	normalized = normalize_guest_session_id(session_id)
	if normalized is None:
		raise UnauthorizedError("Invalid guest session id.")
	if not await validate_guest_session(normalized, redis=redis):
		raise UnauthorizedError("Guest session expired or invalid.")
	return normalized


def normalize_guest_session_id(session_id: str) -> str | None:
	"""Return canonical UUID string or None if the value is not a valid UUID."""
	if not session_id or not session_id.strip():
		return None
	try:
		return str(uuid.UUID(session_id.strip()))
	except ValueError:
		return None


async def create_guest_session(
	*,
	redis: aioredis.Redis | None = None,
) -> GuestSessionInfo:
	"""Create a new guest session id and register it in Redis with TTL."""
	settings = get_settings()
	client = redis if redis is not None else await get_redis()
	session_id = str(uuid.uuid4())
	ttl = settings.GUEST_SESSION_TTL_SECONDS
	payload = json.dumps({"created_at": datetime.now(timezone.utc).isoformat()})
	await client.set(_session_key(session_id), payload, ex=ttl)
	return GuestSessionInfo(guest_session_id=session_id, expires_in=ttl)


async def get_guest_session(
	session_id: str,
	*,
	redis: aioredis.Redis | None = None,
) -> GuestSessionInfo | None:
	"""Return session metadata when the id is valid and still present in Redis."""
	normalized = normalize_guest_session_id(session_id)
	if normalized is None:
		return None
	client = redis if redis is not None else await get_redis()
	key = _session_key(normalized)
	if not await client.exists(key):
		return None
	ttl = await client.ttl(key)
	if ttl is None or ttl < 0:
		return None
	return GuestSessionInfo(guest_session_id=normalized, expires_in=int(ttl))


async def validate_guest_session(
	session_id: str,
	*,
	redis: aioredis.Redis | None = None,
) -> bool:
	"""Return True when the session id exists and has not expired in Redis."""
	normalized = normalize_guest_session_id(session_id)
	if normalized is None:
		return False
	client = redis if redis is not None else await get_redis()
	return bool(await client.exists(_session_key(normalized)))


async def touch_guest_session(
	session_id: str,
	*,
	redis: aioredis.Redis | None = None,
) -> bool:
	"""Refresh session TTL on activity. Returns False if the session is missing or invalid."""
	normalized = normalize_guest_session_id(session_id)
	if normalized is None:
		return False
	client = redis if redis is not None else await get_redis()
	key = _session_key(normalized)
	if not await client.exists(key):
		return False
	ttl = get_settings().GUEST_SESSION_TTL_SECONDS
	return bool(await client.expire(key, ttl))


async def revoke_guest_session(
	session_id: str,
	*,
	redis: aioredis.Redis | None = None,
) -> bool:
	"""Remove a guest session from Redis (e.g. after migration to a user account)."""
	normalized = normalize_guest_session_id(session_id)
	if normalized is None:
		return False
	client = redis if redis is not None else await get_redis()
	deleted = await client.delete(_session_key(normalized))
	return deleted > 0


async def migrate_guest_session_to_user(
	case_repo: MedicalCaseRepository,
	chat_repo: ChatRepository,
	*,
	guest_session_id: str,
	user_id: UUID,
	redis: aioredis.Redis | None = None,
) -> GuestMigrationResult:
	"""Attach guest cases and orphan chats to the authenticated user; clear guest session."""
	normalized = normalize_guest_session_id(guest_session_id)
	if normalized is None:
		return GuestMigrationResult(cases_migrated=0, chats_updated=0)

	cases = await case_repo.get_by_guest_session(normalized, offset=0, limit=500)
	if not cases:
		await revoke_guest_session(normalized, redis=redis)
		return GuestMigrationResult(cases_migrated=0, chats_updated=0)

	case_ids: list[UUID] = []
	for case in cases:
		case.user_id = user_id
		case.guest_session_id = None
		case_ids.append(case.id)

	chats_updated = await chat_repo.assign_user_to_case_messages(case_ids, user_id)
	await case_repo.commit()
	await revoke_guest_session(normalized, redis=redis)
	return GuestMigrationResult(cases_migrated=len(cases), chats_updated=chats_updated)
