from uuid import UUID

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from app.models.doctor_profile import DoctorProfile
from app.repositories.base import BaseRepository


class DoctorProfileRepository(BaseRepository[DoctorProfile]):
	model = DoctorProfile

	async def get_by_user_id(self, user_id: UUID) -> DoctorProfile | None:
		result = await self._session.execute(select(DoctorProfile).where(DoctorProfile.user_id == user_id))
		return result.scalars().first()

	async def get_or_create(self, user_id: UUID) -> DoctorProfile:
		"""Return existing profile or create a blank one."""
		profile = await self.get_by_user_id(user_id)
		if profile is None:
			try:
				profile = DoctorProfile(user_id=user_id)
				self._session.add(profile)
				await self._session.flush()
			except IntegrityError:
				await self._session.rollback()
				profile = await self.get_by_user_id(user_id)
				if profile is None:
					raise
		return profile
