from uuid import UUID

from sqlalchemy import func, select, update

from app.models.chat import Chat, SenderType
from app.repositories.base import BaseRepository


class ChatRepository(BaseRepository[Chat]):
	model = Chat

	async def list_by_case(
		self,
		medical_case_id: UUID,
		*,
		offset: int = 0,
		limit: int = 50,
	) -> list[Chat]:
		result = await self._session.execute(
			select(Chat)
			.where(Chat.medical_case_id == medical_case_id)
			.order_by(Chat.sent_at.asc())
			.offset(offset)
			.limit(limit)
		)
		return list(result.scalars().all())

	async def count_by_case(self, medical_case_id: UUID) -> int:
		result = await self._session.execute(
			select(func.count()).select_from(Chat).where(Chat.medical_case_id == medical_case_id)
		)
		return result.scalar_one()

	async def assign_user_to_case_messages(self, case_ids: list[UUID], user_id: UUID) -> int:
		if not case_ids:
			return 0
		result = await self._session.execute(
			update(Chat).where(Chat.medical_case_id.in_(case_ids), Chat.user_id.is_(None)).values(user_id=user_id)
		)
		return int(result.rowcount or 0)

	async def count_patient_messages_by_case(self, medical_case_id: UUID) -> int:
		result = await self._session.execute(
			select(func.count())
			.select_from(Chat)
			.where(
				Chat.medical_case_id == medical_case_id,
				Chat.sender_type == SenderType.PATIENT,
			)
		)
		return int(result.scalar_one())

	async def list_by_user(
		self,
		user_id: UUID,
		*,
		offset: int = 0,
		limit: int = 50,
	) -> list[Chat]:
		result = await self._session.execute(
			select(Chat).where(Chat.user_id == user_id).order_by(Chat.sent_at.desc()).offset(offset).limit(limit)
		)
		return list(result.scalars().all())

	async def get_recent_for_case(
		self,
		medical_case_id: UUID,
		*,
		limit: int = 50,
	) -> list[Chat]:
		"""
		Fetch the most recent `limit` messages in chronological order.

		Used on WebSocket reconnect to send history to the client.
		We want the LAST 50 messages, but displayed oldest-first.

		Approach: subquery gets the IDs of the newest `limit` rows,
		outer query fetches the full ORM objects ordered ascending.
		Avoids aliased() which has unreliable behaviour with async ORM.
		"""
		id_subq = (
			select(Chat.id)
			.where(Chat.medical_case_id == medical_case_id)
			.order_by(Chat.sent_at.desc())
			.limit(limit)
			.subquery()
		)
		result = await self._session.execute(
			select(Chat).where(Chat.id.in_(select(id_subq))).order_by(Chat.sent_at.asc())
		)
		return list(result.scalars().all())
