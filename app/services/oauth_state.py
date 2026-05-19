"""Signed OAuth state tokens (guest migration + CSRF nonce + optional device id)."""

from __future__ import annotations

import secrets
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any

import jwt

from app.core.config import get_settings
from app.services.guest import normalize_guest_session_id


@dataclass(frozen=True)
class OAuthStatePayload:
	guest_session_id: str | None
	device_id: str | None
	platform: str | None


def create_oauth_state(
	*,
	guest_session_id: str | None = None,
	device_id: str | None = None,
	platform: str | None = None,
) -> str:
	"""Build a short-lived signed state value for the OAuth redirect."""
	settings = get_settings()
	now = datetime.now(timezone.utc)
	expires_at = now + timedelta(minutes=settings.OAUTH_STATE_EXPIRES_MINUTES)

	normalized_guest: str | None = None
	if guest_session_id and guest_session_id.strip():
		normalized_guest = normalize_guest_session_id(guest_session_id.strip())

	payload: dict[str, Any] = {
		"type": "oauth_state",
		"nonce": secrets.token_urlsafe(16),
		"iat": int(now.timestamp()),
		"exp": int(expires_at.timestamp()),
	}
	if normalized_guest:
		payload["guest"] = normalized_guest
	if device_id and device_id.strip():
		payload["device"] = device_id.strip()[:255]
	if platform and platform.strip():
		payload["platform"] = platform.strip()[:32]

	return jwt.encode(payload, settings.JWT_SECRET, algorithm=settings.JWT_ALGORITHM)


def decode_oauth_state(state: str) -> OAuthStatePayload | None:
	"""Verify OAuth state and return embedded guest session / device metadata."""
	if not state or not state.strip():
		return OAuthStatePayload(guest_session_id=None, device_id=None, platform=None)

	settings = get_settings()
	try:
		payload = jwt.decode(
			state,
			settings.JWT_SECRET,
			algorithms=[settings.JWT_ALGORITHM],
			options={"require": ["exp", "iat", "type", "nonce"]},
		)
	except jwt.PyJWTError:
		return None

	if payload.get("type") != "oauth_state":
		return None

	guest_raw = payload.get("guest")
	guest_id: str | None = None
	if guest_raw:
		try:
			guest_id = str(uuid.UUID(str(guest_raw)))
		except ValueError:
			guest_id = normalize_guest_session_id(str(guest_raw))

	device_raw = payload.get("device")
	device_id = str(device_raw).strip()[:255] if device_raw else None

	platform_raw = payload.get("platform")
	platform = str(platform_raw).strip()[:32] if platform_raw else None

	return OAuthStatePayload(guest_session_id=guest_id, device_id=device_id, platform=platform)
