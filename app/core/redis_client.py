"""Optional async Redis client (Celery broker URL). Guest sessions use Postgres only."""

from __future__ import annotations

import redis.asyncio as aioredis

from app.core.config import get_settings

_redis: aioredis.Redis | None = None


async def get_redis() -> aioredis.Redis:
	"""Return a process-wide async Redis client (lazy init from CELERY_BROKER_URL)."""
	global _redis
	if _redis is None:
		settings = get_settings()
		_redis = aioredis.from_url(
			settings.CELERY_BROKER_URL,
			decode_responses=True,
		)
	return _redis


async def close_redis() -> None:
	"""Close the shared client (tests and app shutdown)."""
	global _redis
	if _redis is not None:
		await _redis.aclose()
		_redis = None
