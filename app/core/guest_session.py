"""Guest session helpers (IP hashing, request metadata)."""

from __future__ import annotations

from fastapi import Request

from app.core.config import get_settings
from app.core.security import hash_opaque_token

DEVICE_FINGERPRINT_HEADER = "X-Device-Fingerprint"


def hash_client_ip(client_ip: str) -> str:
	"""Hash a client IP for storage (peppered SHA-256)."""
	pepper = get_settings().OTP_PEPPER
	return hash_opaque_token(f"{pepper}:guest-ip:{client_ip}")


def get_client_ip(request: Request) -> str:
	"""Resolve client IP, honoring X-Forwarded-For when present."""
	forwarded = request.headers.get("X-Forwarded-For")
	if forwarded:
		return forwarded.split(",")[0].strip()
	if request.client is not None and request.client.host:
		return request.client.host
	return "unknown"


def normalize_device_fingerprint(value: str | None) -> str | None:
	if value is None:
		return None
	stripped = value.strip()
	if not stripped:
		return None
	return stripped[:255]
