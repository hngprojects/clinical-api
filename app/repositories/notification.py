from collections.abc import AsyncIterator
from datetime import datetime, timezone
from uuid import UUID

from sqlalchemy import func, select

from app.models.notification import Notification
from app.repositories.base import BaseRepository


class NotificationRepository(BaseRepository[Notification]):
	model = Notification

	async def list_by_user(
		self,
		user_id: UUID,
		*,
		unread_only: bool = False,
		offset: int = 0,
		limit: int = 50,
	) -> list[Notification]:
		stmt = (
			select(Notification)
			.where(Notification.user_id == user_id)
			.order_by(Notification.created_at.desc())
			.offset(offset)
			.limit(limit)
		)
		if unread_only:
			stmt = stmt.where(Notification.is_read.is_(False))
		result = await self._session.execute(stmt)
		return list(result.scalars().all())

	async def get_by_user_case_and_type(
		self,
		user_id: UUID,
		medical_case_id: UUID | None,
		notification_type,
	) -> Notification | None:
		stmt = select(Notification).where(
			Notification.user_id == user_id,
			Notification.medical_case_id == medical_case_id,
			Notification.type == notification_type,
		)
		result = await self._session.execute(stmt)
		return result.scalar_one_or_none()

	async def stream_after(
		self,
		user_id: UUID,
		created_after: datetime | None,
		*,
		last_id: UUID | None = None,
		created_before: datetime | None = None,
	) -> AsyncIterator[Notification]:
		stmt = select(Notification).where(Notification.user_id == user_id)
		if created_after is not None:
			if last_id is not None:
				stmt = stmt.where(
					(Notification.created_at > created_after)
					| ((Notification.created_at == created_after) & (Notification.id > last_id))
				)
			else:
				stmt = stmt.where(Notification.created_at > created_after)
		if created_before is not None:
			stmt = stmt.where(Notification.created_at <= created_before)
		stmt = stmt.order_by(Notification.created_at.asc(), Notification.id.asc())
		stream = await self._session.stream_scalars(stmt)
		async for notification in stream:
			yield notification

	async def count_unread(self, user_id: UUID) -> int:
		result = await self._session.execute(
			select(func.count())
			.select_from(Notification)
			.where(Notification.user_id == user_id, Notification.is_read.is_(False))
		)
		return result.scalar_one()

	async def mark_read(self, notification_id: UUID) -> Notification | None:
		notif = await self.get_by_id(notification_id)
		if notif is None:
			return None
		notif.is_read = True
		notif.read_at = datetime.now(timezone.utc)
		return notif

	async def mark_delivered(self, notification_id: UUID) -> Notification | None:
		notif = await self.get_by_id(notification_id)
		if notif is None:
			return None
		notif.delivered_at = datetime.now(timezone.utc)
		return notif
