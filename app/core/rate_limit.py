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


def _login_failure_key(ip_hash: str) -> str:
	return f"rl:login-fail:{ip_hash}"


async def assert_login_not_rate_limited(ip_hash: str) -> None:
	"""Block login when too many recent failures were recorded for this IP."""
	settings = get_settings()
	redis = await get_redis()
	raw = await redis.get(_login_failure_key(ip_hash))
	count = int(raw) if raw else 0
	if count > settings.LOGIN_FAILURE_RATE_LIMIT:
		raise RateLimitExceeded(
			"Too many failed login attempts. Please try again later.",
		)


async def record_login_failure(ip_hash: str) -> None:
	"""Increment the failed-login counter for this IP."""
	settings = get_settings()
	redis = await get_redis()
	key = _login_failure_key(ip_hash)
	count = await redis.incr(key)
	if count == 1:
		await redis.expire(key, settings.LOGIN_FAILURE_RATE_WINDOW_SECONDS)
	if count > settings.LOGIN_FAILURE_RATE_LIMIT:
		raise RateLimitExceeded(
			"Too many failed login attempts. Please try again later.",
		)


async def clear_login_failures(ip_hash: str) -> None:
	"""Reset failed-login counter after a successful login."""
	redis = await get_redis()
	await redis.delete(_login_failure_key(ip_hash))
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


def _login_failure_key(ip_hash: str) -> str:
    return f"rl:login-fail:{ip_hash}"


async def assert_login_not_rate_limited(ip_hash: str) -> None:
    """Block login when too many recent failures were recorded for this IP."""
    settings = get_settings()
    redis = await get_redis()

    raw = await redis.get(_login_failure_key(ip_hash))
    count = int(raw) if raw else 0

    if count > settings.LOGIN_FAILURE_RATE_LIMIT:
        raise RateLimitExceeded(
            "Too many failed login attempts. Please try again later.",
        )


async def record_login_failure(ip_hash: str) -> None:
    """Increment the failed-login counter for this IP."""
    settings = get_settings()
    redis = await get_redis()

    key = _login_failure_key(ip_hash)
    count = await redis.incr(key)

    if count == 1:
        await redis.expire(key, settings.LOGIN_FAILURE_RATE_WINDOW_SECONDS)

    if count > settings.LOGIN_FAILURE_RATE_LIMIT:
        raise RateLimitExceeded(
            "Too many failed login attempts. Please try again later.",
        )


async def clear_login_failures(ip_hash: str) -> None:
    """Reset failed-login counter after a successful login."""
    redis = await get_redis()
    await redis.delete(_login_failure_key(ip_hash))


# NEW: Generic reusable rate limit helper 

async def enforce_action_rate_limit(
    *,
    key: str,
    limit: int,
    window_seconds: int,
    message: str,
) -> None:
    """
    Generic reusable rate limiter for:
    - OTP resend
    - OTP verify
    - forgot password
    - reset password
    - email update flows
    """
    redis = await get_redis()

    count = await redis.incr(key)

    if count == 1:
        await redis.expire(key, window_seconds)

    if count > limit:
        raise RateLimitExceeded(message)


# NEW: Rate limit key helpers (used across auth endpoints)

def _forgot_password_key(ip_hash: str) -> str:
    return f"rl:forgot-password:{ip_hash}"


def _resend_otp_key(ip_hash: str) -> str:
    return f"rl:resend-otp:{ip_hash}"


def _verify_otp_key(ip_hash: str) -> str:
    return f"rl:verify-otp:{ip_hash}"


def _reset_password_key(ip_hash: str) -> str:
    return f"rl:reset-password:{ip_hash}"

def _verify_otp_fail_key(ip_hash: str) -> str:
    return f"rl:verify-otp-fail:{ip_hash}"

async def record_verify_otp_failure(ip_hash: str):
    redis = await get_redis()
    key = _verify_otp_fail_key(ip_hash)
    count = await redis.incr(key)
    if count == 1:
        await redis.expire(key, 300)
    if count > 5:
        raise RateLimitExceeded("Too many OTP attempts")