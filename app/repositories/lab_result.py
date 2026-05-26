from typing import Sequence
from uuid import UUID

from sqlalchemy import func, select

from app.models.lab_result import LabResult, OCRStatus
from app.repositories.base import BaseRepository


class LabResultRepository(BaseRepository[LabResult]):
	model = LabResult

	async def first_by_case(self, medical_case_id: UUID) -> LabResult | None:
		result = await self._session.execute(
			select(LabResult)
			.where(LabResult.medical_case_id == medical_case_id)
			.order_by(LabResult.created_at.asc(), LabResult.id.asc())
			.limit(1)
		)
		return result.scalar_one_or_none()

	async def first_by_case_ids(self, medical_case_ids: Sequence[UUID]) -> dict[UUID, LabResult]:
		if not medical_case_ids:
			return {}

		ranked_results = (
			select(
				LabResult.id.label("lab_result_id"),
				LabResult.medical_case_id.label("medical_case_id"),
				func.row_number()
				.over(
					partition_by=LabResult.medical_case_id,
					order_by=(LabResult.created_at.asc(), LabResult.id.asc()),
				)
				.label("row_number"),
			)
			.where(LabResult.medical_case_id.in_(medical_case_ids))
			.subquery()
		)

		result = await self._session.execute(
			select(ranked_results.c.medical_case_id, LabResult)
			.select_from(ranked_results)
			.join(LabResult, LabResult.id == ranked_results.c.lab_result_id)
			.where(ranked_results.c.row_number == 1)
		)
		return {medical_case_id: lab_result for medical_case_id, lab_result in result.all()}

	async def list_by_case(
		self,
		medical_case_id: UUID,
		*,
		offset: int = 0,
		limit: int = 50,
	) -> list[LabResult]:
		result = await self._session.execute(
			select(LabResult)
			.where(LabResult.medical_case_id == medical_case_id)
			.order_by(LabResult.created_at.desc())
			.offset(offset)
			.limit(limit)
		)
		return list(result.scalars().all())

	async def count_by_case(self, medical_case_id: UUID) -> int:
		result = await self._session.execute(
			select(func.count()).select_from(LabResult).where(LabResult.medical_case_id == medical_case_id)
		)
		return result.scalar_one()

	async def update_ocr_status(self, result_id: UUID, status: OCRStatus) -> LabResult | None:
		lab_result = await self.get_by_id(result_id)
		if lab_result is None:
			return None
		lab_result.ocr_status = status
		return lab_result
