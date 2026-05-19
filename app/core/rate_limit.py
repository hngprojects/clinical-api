"""Redis-backed fixed-window rate limiting."""

from __future__ import annotations

from app.core.config import get_settings
from app.core.exceptions import RateLimitExceeded
from app.core.redis_client import get_redis


async def enforce_rate_limit(*, key: str, limit: int, window_seconds: int) -> None:
	"""Increment a counter for `key` and raise RateLimitExceeded when over `limit`."""
	redis = await get_redis()
	count = await redis.incr(key)
	if count == 1:
		await redis.expire(key, window_seconds)
	if count > limit:
		raise RateLimitExceeded()


async def enforce_guest_session_create_limit(ip_hash: str) -> None:
	"""Rate-limit POST /guest-session per hashed client IP."""
	settings = get_settings()
	await enforce_rate_limit(
		key=f"rl:guest-session:create:{ip_hash}",
		limit=settings.GUEST_SESSION_CREATE_RATE_LIMIT,
		window_seconds=settings.GUEST_SESSION_CREATE_RATE_WINDOW_SECONDS,
	)
