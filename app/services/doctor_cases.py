from uuid import UUID

from app.core.config import get_settings
from app.core.exceptions import NotFoundError
from app.models.medical_case import DoctorCaseStatus, MedicalCase
from app.repositories.medical_case import MedicalCaseRepository


async def list_doctor_cases(
	repo: MedicalCaseRepository,
	doctor_id: UUID,
	*,
	case_status: DoctorCaseStatus,
	preview: bool,
	offset: int,
	limit: int,
) -> list[tuple]:
	cap = get_settings().DOCTOR_CASE_PREVIEW_LIMIT if preview else limit
	return await repo.list_doctor_preview(doctor_id, case_status=case_status, limit=min(limit, cap), offset=offset)


async def accept_doctor_case(repo: MedicalCaseRepository, case_id: UUID, doctor_id: UUID) -> MedicalCase:
	case = await repo.transition_doctor_case(
		case_id, doctor_id, expected=DoctorCaseStatus.PENDING, target=DoctorCaseStatus.ACCEPTED
	)
	if case is None:
		raise NotFoundError("Pending case request not found.")
	await repo.commit()
	return case


async def decline_doctor_case(repo: MedicalCaseRepository, case_id: UUID, doctor_id: UUID) -> MedicalCase:
	case = await repo.transition_doctor_case(
		case_id, doctor_id, expected=DoctorCaseStatus.PENDING, target=DoctorCaseStatus.DECLINED
	)
	if case is None:
		raise NotFoundError("Pending case request not found.")
	await repo.commit()
	return case
