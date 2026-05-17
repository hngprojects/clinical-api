"""Guest session lifecycle backed by Redis TTL keys."""

from __future__ import annotations

import json
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone

import redis.asyncio as aioredis

from app.core.config import get_settings
from app.core.redis_client import get_redis

GUEST_SESSION_KEY_PREFIX = "guest:session:"


@dataclass(frozen=True)
class GuestSessionInfo:
	"""Issued guest session metadata returned to clients."""

	guest_session_id: str
	expires_in: int


def _session_key(session_id: str) -> str:
	return f"{GUEST_SESSION_KEY_PREFIX}{session_id}"


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
