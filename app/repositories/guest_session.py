from datetime import datetime, timezone

from sqlalchemy import select

from app.models.guest_session import GuestSession
from app.repositories.base import BaseRepository


class GuestSessionRepository(BaseRepository[GuestSession]):
	model = GuestSession

	async def get_by_session_id(self, session_id: str) -> GuestSession | None:
		result = await self._session.execute(select(GuestSession).where(GuestSession.session_id == session_id))
		return result.scalar_one_or_none()

	async def is_valid(self, session_id: str) -> bool:
		"""Check if a guest session exists and has not expired."""
		session = await self.get_by_session_id(session_id)
		if session is None:
			return False
		return session.expires_at > datetime.now(timezone.utc)

	async def touch(self, session_id: str) -> GuestSession | None:
		"""Update last_active_at and extend expiry by 1 hour."""
		session = await self.get_by_session_id(session_id)
		if session is None:
			return None
		now = datetime.now(timezone.utc)
		from datetime import timedelta

		if session.expires_at <= now:
			return None

		session.last_active_at = now
		session.expires_at = now + timedelta(hours=1)
		return session
