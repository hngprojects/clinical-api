from uuid import UUID

from sqlalchemy import func, select

from app.models.medical_case import DoctorCaseStatus, MedicalCase, MedicalCaseStatus
from app.models.user import User
from app.repositories.base import BaseRepository


class MedicalCaseRepository(BaseRepository[MedicalCase]):
	model = MedicalCase

	async def list_doctor_preview(
		self, doctor_id: UUID, *, case_status: DoctorCaseStatus, limit: int, offset: int = 0
	) -> list[tuple]:
		"""Fetch only scalar fields needed by the doctor dashboard preview."""
		order_column = MedicalCase.created_at if case_status == DoctorCaseStatus.PENDING else MedicalCase.updated_at
		result = await self._session.execute(
			select(
				MedicalCase.id,
				MedicalCase.user_id,
				MedicalCase.title,
				User.first_name,
				User.last_name,
				User.avatar_url,
				MedicalCase.doctor_case_status,
				MedicalCase.created_at,
				MedicalCase.updated_at,
			)
			.outerjoin(User, User.id == MedicalCase.user_id)
			.where(MedicalCase.doctor_id == doctor_id, MedicalCase.doctor_case_status == case_status)
			.order_by(order_column.desc(), MedicalCase.id.desc())
			.offset(offset)
			.limit(limit)
		)
		return list(result.all())

	async def transition_doctor_case(
		self, case_id: UUID, doctor_id: UUID, *, expected: DoctorCaseStatus, target: DoctorCaseStatus
	) -> MedicalCase | None:
		"""Lock and transition only the assigned doctor's currently expected state."""
		case = await self._session.scalar(
			select(MedicalCase)
			.where(
				MedicalCase.id == case_id,
				MedicalCase.doctor_id == doctor_id,
				MedicalCase.doctor_case_status == expected,
			)
			.with_for_update()
		)
		if case is None:
			return None
		case.doctor_case_status = target
		return case

	async def list_by_user(
		self,
		user_id: UUID,
		*,
		offset: int = 0,
		limit: int = 50,
	) -> list[MedicalCase]:
		result = await self._session.execute(
			select(MedicalCase)
			.where(MedicalCase.user_id == user_id)
			.order_by(MedicalCase.created_at.desc())
			.offset(offset)
			.limit(limit)
		)
		return list(result.scalars().all())

	async def count_by_user(self, user_id: UUID) -> int:
		result = await self._session.execute(
			select(func.count()).select_from(MedicalCase).where(MedicalCase.user_id == user_id)
		)
		return result.scalar_one()

	async def get_by_guest_session(
		self,
		guest_session_id: UUID,
		*,
		offset: int = 0,
		limit: int = 50,
	) -> list[MedicalCase]:
		result = await self._session.execute(
			select(MedicalCase)
			.where(MedicalCase.guest_session_id == guest_session_id)
			.order_by(MedicalCase.created_at.desc())
			.offset(offset)
			.limit(limit)
		)
		return list(result.scalars().all())

	async def count_by_guest_session(self, guest_session_id: UUID) -> int:
		result = await self._session.execute(
			select(func.count()).select_from(MedicalCase).where(MedicalCase.guest_session_id == guest_session_id)
		)
		return int(result.scalar_one())

	async def update_status(self, case_id: UUID, status: MedicalCaseStatus) -> MedicalCase | None:
		case = await self.get_by_id(case_id)
		if case is None:
			return None
		case.status = status
		return case
