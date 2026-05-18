from datetime import datetime
from uuid import UUID

from sqlalchemy import select

from app.models.guest_session import GuestSession
from app.repositories.base import BaseRepository


class GuestSessionRepository(BaseRepository[GuestSession]):
	model = GuestSession

	async def get_by_id(self, guest_session_id: UUID) -> GuestSession | None:
		return await self._session.get(GuestSession, guest_session_id)

	async def get_active_by_device(
		self,
		ip_hash: str,
		device_fingerprint: str | None,
		*,
		now: datetime,
	) -> GuestSession | None:
		stmt = (
			select(GuestSession)
			.where(
				GuestSession.ip_hash == ip_hash,
				GuestSession.device_fingerprint == device_fingerprint,
				GuestSession.revoked.is_(False),
				GuestSession.migrated_user_id.is_(None),
				GuestSession.expires_at > now,
			)
			.order_by(GuestSession.last_active_at.desc())
			.limit(1)
		)
		result = await self._session.execute(stmt)
		return result.scalar_one_or_none()
