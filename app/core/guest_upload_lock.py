"""Redis lock: one in-flight guest upload per guest session."""

from __future__ import annotations

from uuid import UUID

from app.core.config import get_settings
from app.core.exceptions import ConflictError
from app.core.redis_client import get_redis


def _lock_key(guest_session_id: UUID) -> str:
	return f"guest:upload:lock:{guest_session_id}"


def _counted_key(case_id: UUID) -> str:
	return f"guest:upload:counted:{case_id}"


async def acquire_guest_upload_lock(guest_session_id: UUID) -> None:
	"""Hold until pipeline completes or fails. Raises ConflictError if already locked."""
	settings = get_settings()
	redis = await get_redis()
	acquired = await redis.set(
		_lock_key(guest_session_id),
		"1",
		nx=True,
		ex=settings.GUEST_UPLOAD_LOCK_SECONDS,
	)
	if not acquired:
		raise ConflictError("An upload is already in progress. Please wait for it to finish.")


async def release_guest_upload_lock(guest_session_id: UUID) -> None:
	redis = await get_redis()
	await redis.delete(_lock_key(guest_session_id))


async def mark_guest_upload_counted(case_id: UUID) -> bool:
	"""Return True if this case was not already counted (idempotent pipeline retries)."""
	settings = get_settings()
	redis = await get_redis()
	key = _counted_key(case_id)
	was_set = await redis.set(key, "1", nx=True, ex=settings.GUEST_UPLOAD_LOCK_SECONDS)
	return bool(was_set)


async def clear_guest_upload_counted(case_id: UUID) -> None:
	redis = await get_redis()
	await redis.delete(_counted_key(case_id))
