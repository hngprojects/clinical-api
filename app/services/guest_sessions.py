"""Guest session lifecycle backed by guest_sessions (Postgres)."""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone
from uuid import UUID

from app.core.config import get_settings
from app.models.guest_session import GuestSession
from app.repositories.guest_session import GuestSessionRepository


class GuestSessionManager:
	"""Create, resolve, and revoke server-owned guest sessions."""

	def __init__(self, repo: GuestSessionRepository) -> None:
		self._repo = repo

	@staticmethod
	def _now() -> datetime:
		return datetime.now(timezone.utc)

	@staticmethod
	def is_active(session: GuestSession, *, now: datetime | None = None) -> bool:
		current = now or GuestSessionManager._now()
		return not session.revoked and session.migrated_user_id is None and session.expires_at > current

	async def create(
		self,
		ip_hash: str,
		device_fingerprint: str | None,
	) -> GuestSession:
		"""Return an active session for this device, creating one if needed."""
		now = self._now()
		existing = await self._repo.get_active_by_device(
			ip_hash,
			device_fingerprint,
			now=now,
		)
		if existing is not None:
			return await self._touch(existing, now=now)

		settings = get_settings()
		session = GuestSession(
			id=uuid.uuid4(),
			ip_hash=ip_hash,
			device_fingerprint=device_fingerprint,
			chat_count=0,
			upload_count=0,
			expires_at=now + timedelta(seconds=settings.GUEST_SESSION_TTL_SECONDS),
			last_active_at=now,
			revoked=False,
		)
		self._repo.add(session)
		await self._repo.commit()
		return session

	async def get(self, guest_session_id: UUID) -> GuestSession | None:
		"""Load a guest session if it exists and is still active."""
		session = await self._repo.get_by_id(guest_session_id)
		if session is None or not self.is_active(session):
			return None
		return session

	async def touch(self, guest_session_id: UUID) -> GuestSession | None:
		"""Extend TTL and update last_active_at for an active session."""
		session = await self.get(guest_session_id)
		if session is None:
			return None
		return await self._touch(session, now=self._now())

	async def revoke(self, guest_session_id: UUID) -> None:
		"""Mark a guest session revoked (e.g. after migration)."""
		session = await self._repo.get_by_id(guest_session_id)
		if session is None:
			return
		session.revoked = True
		await self._repo.commit()

	async def _touch(self, session: GuestSession, *, now: datetime) -> GuestSession:
		settings = get_settings()
		session.last_active_at = now
		session.expires_at = now + timedelta(seconds=settings.GUEST_SESSION_TTL_SECONDS)
		await self._repo.commit()
		return session
