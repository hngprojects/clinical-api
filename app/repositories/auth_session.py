from uuid import UUID

from sqlalchemy import select

from app.models.auth_session import AuthSession
from app.repositories.base import BaseRepository


class AuthSessionRepository(BaseRepository[AuthSession]):
	model = AuthSession

	async def get_by_refresh_token(self, refresh_token: str) -> AuthSession | None:
		result = await self._session.execute(select(AuthSession).where(AuthSession.refresh_token == refresh_token))
		return result.scalar_one_or_none()

	async def list_by_user(self, user_id: UUID) -> list[AuthSession]:
		result = await self._session.execute(
			select(AuthSession).where(AuthSession.user_id == user_id).order_by(AuthSession.created_at.desc())
		)
		return list(result.scalars().all())
