from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.user import User, UserRole


class UserRepository:
	"""Encapsulates all database operations for the User model."""

	def __init__(self, session: AsyncSession) -> None:
		self._session = session

	async def get_by_id(self, user_id: UUID) -> User | None:
		return await self._session.get(User, user_id)

	async def get_by_email(self, email: str, role: UserRole) -> User | None:
		normalized = email.strip().lower()
		stmt = select(User).where(User.email == normalized, User.role == role)
		result = await self._session.execute(stmt)
		return result.scalar_one_or_none()

	async def get_by_email_and_role(self, email: str, role: UserRole) -> User | None:
		return await self.get_by_email(email, role)

	async def count_by_email(self, email: str, *, exclude_user_id: UUID | None = None) -> int:
		normalized = email.strip().lower()
		stmt = select(User).where(User.email == normalized)
		if exclude_user_id is not None:
			stmt = stmt.where(User.id != exclude_user_id)
		result = await self._session.execute(stmt)
		return len(result.scalars().all())

	async def get_by_google_id(self, google_id: str, role: UserRole) -> User | None:
		stmt = select(User).where(User.google_id == google_id, User.role == role)
		result = await self._session.execute(stmt)
		return result.scalar_one_or_none()

	async def get_by_google_id_and_role(self, google_id: str, role: UserRole) -> User | None:
		return await self.get_by_google_id(google_id, role)

	def add(self, user: User) -> None:
		self._session.add(user)

	async def flush(self) -> None:
		await self._session.flush()

	async def commit(self) -> None:
		await self._session.commit()

	async def refresh(self, user: User) -> None:
		await self._session.refresh(user)

	async def delete(self, user: User) -> None:
		await self._session.delete(user)

	async def rollback(self) -> None:
		await self._session.rollback()
