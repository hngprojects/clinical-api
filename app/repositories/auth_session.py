from datetime import datetime
from uuid import UUID

from sqlalchemy import select, update

from app.models.auth_session import AuthSession
from app.repositories.base import BaseRepository


class AuthSessionRepository(BaseRepository[AuthSession]):
	model = AuthSession

	async def get_by_refresh_token(self, refresh_token_hash: str) -> AuthSession | None:
		result = await self._session.execute(select(AuthSession).where(AuthSession.refresh_token == refresh_token_hash))
		return result.scalar_one_or_none()

	async def get_by_id_for_user(self, session_id: UUID, user_id: UUID) -> AuthSession | None:
		result = await self._session.execute(
			select(AuthSession).where(
				AuthSession.id == session_id,
				AuthSession.user_id == user_id,
			)
		)
		return result.scalar_one_or_none()

	async def get_active_by_user_and_device(self, user_id: UUID, device_id: str) -> AuthSession | None:
		result = await self._session.execute(
			select(AuthSession).where(
				AuthSession.user_id == user_id,
				AuthSession.device_id == device_id,
				AuthSession.revoked.is_(False),
			)
		)
		return result.scalar_one_or_none()

	async def list_active_by_user(self, user_id: UUID) -> list[AuthSession]:
		result = await self._session.execute(
			select(AuthSession)
			.where(AuthSession.user_id == user_id, AuthSession.revoked.is_(False))
			.order_by(AuthSession.last_used_at.desc().nullslast(), AuthSession.created_at.desc())
		)
		return list(result.scalars().all())

	async def list_by_user(self, user_id: UUID) -> list[AuthSession]:
		result = await self._session.execute(
			select(AuthSession).where(AuthSession.user_id == user_id).order_by(AuthSession.created_at.desc())
		)
		return list(result.scalars().all())

	async def revoke_all_for_user(self, user_id: UUID, *, revoked_at: datetime) -> int:
		result = await self._session.execute(
			update(AuthSession)
			.where(AuthSession.user_id == user_id, AuthSession.revoked.is_(False))
			.values(revoked=True, revoked_at=revoked_at)
		)
		return int(result.rowcount or 0)
