from datetime import datetime, timedelta
from uuid import UUID

from sqlalchemy import delete, or_, select, update

from app.models.guest_session import GuestSession
from app.repositories.base import BaseRepository


class GuestSessionRepository(BaseRepository[GuestSession]):
	model = GuestSession

	async def get_by_id(self, guest_session_id: UUID) -> GuestSession | None:
		return await self._session.get(GuestSession, guest_session_id)

	async def get_by_id_for_update(self, guest_session_id: UUID) -> GuestSession | None:
		result = await self._session.execute(
			select(GuestSession).where(GuestSession.id == guest_session_id).with_for_update()
		)
		return result.scalar_one_or_none()

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

	async def try_increment_counter(
		self,
		guest_session_id: UUID,
		*,
		column: str,
		limit: int,
		now: datetime,
		ttl_seconds: int,
	) -> bool:
		"""Atomically increment chat_count or upload_count when under limit and session is active."""
		if column not in ("chat_count", "upload_count"):
			raise ValueError("column must be 'chat_count' or 'upload_count'")

		new_expires = now + timedelta(seconds=ttl_seconds)
		base_where = (
			GuestSession.id == guest_session_id,
			GuestSession.revoked.is_(False),
			GuestSession.migrated_user_id.is_(None),
			GuestSession.expires_at > now,
		)
		if column == "chat_count":
			stmt = (
				update(GuestSession)
				.where(*base_where, GuestSession.chat_count < limit)
				.values(
					chat_count=GuestSession.chat_count + 1,
					last_active_at=now,
					expires_at=new_expires,
				)
			)
		else:
			stmt = (
				update(GuestSession)
				.where(*base_where, GuestSession.upload_count < limit)
				.values(
					upload_count=GuestSession.upload_count + 1,
					last_active_at=now,
					expires_at=new_expires,
				)
			)
		result = await self._session.execute(stmt)
		return bool(result.rowcount)

	async def delete_stale(self, *, cutoff: datetime) -> int:
		"""Remove expired or long-revoked guest session rows."""
		result = await self._session.execute(
			delete(GuestSession).where(
				or_(
					GuestSession.expires_at < cutoff,
					(GuestSession.revoked.is_(True)) & (GuestSession.last_active_at < cutoff),
				)
			)
		)
		return int(result.rowcount or 0)
