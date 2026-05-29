"""Redis-backed fixed-window rate limiting."""

from __future__ import annotations

from app.core.config import get_settings
from app.core.exceptions import RateLimitExceeded
from app.core.redis_client import get_redis


async def enforce_rate_limit(*, key: str, limit: int, window_seconds: int) -> None:
	redis = await get_redis()
	count = await redis.incr(key)
	if count == 1:
		await redis.expire(key, window_seconds)
	if count > limit:
		raise RateLimitExceeded()


async def enforce_guest_session_create_limit(ip_hash: str) -> None:
	settings = get_settings()
	await enforce_rate_limit(
		key=f"rl:guest-session:create:{ip_hash}",
		limit=settings.GUEST_SESSION_CREATE_RATE_LIMIT,
		window_seconds=settings.GUEST_SESSION_CREATE_RATE_WINDOW_SECONDS,
	)


def _login_failure_key(ip_hash: str) -> str:
	return f"rl:login-fail:{ip_hash}"


async def assert_login_not_rate_limited(ip_hash: str) -> None:
	settings = get_settings()
	redis = await get_redis()
	raw = await redis.get(_login_failure_key(ip_hash))
	count = int(raw) if raw else 0
	if count > settings.LOGIN_FAILURE_RATE_LIMIT:
		raise RateLimitExceeded("Too many failed login attempts. Please try again later.")


async def record_login_failure(ip_hash: str) -> None:
	settings = get_settings()
	redis = await get_redis()
	key = _login_failure_key(ip_hash)
	count = await redis.incr(key)
	if count == 1:
		await redis.expire(key, settings.LOGIN_FAILURE_RATE_WINDOW_SECONDS)
	if count > settings.LOGIN_FAILURE_RATE_LIMIT:
		raise RateLimitExceeded("Too many failed login attempts. Please try again later.")


async def clear_login_failures(ip_hash: str) -> None:
	redis = await get_redis()
	await redis.delete(_login_failure_key(ip_hash))


async def enforce_action_rate_limit(*, key: str, limit: int, window_seconds: int, message: str) -> None:
	redis = await get_redis()
	count = await redis.incr(key)
	if count == 1:
		await redis.expire(key, window_seconds)
	if count > limit:
		raise RateLimitExceeded(message)


def _verify_otp_fail_key(ip_hash: str) -> str:
	return f"rl:verify-otp-fail:{ip_hash}"


async def record_verify_otp_failure(ip_hash: str) -> None:
	settings = get_settings()
	redis = await get_redis()
	key = _verify_otp_fail_key(ip_hash)
	count = await redis.incr(key)
	if count == 1:
		await redis.expire(key, settings.OTP_FAILURE_RATE_WINDOW_SECONDS)
	if count > settings.OTP_FAILURE_RATE_LIMIT:
		raise RateLimitExceeded("Too many OTP attempts")
