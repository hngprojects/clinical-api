from uuid import UUID

from fastapi import APIRouter, Query

from app.api.deps import ApprovedDoctor, MedicalCaseRepo
from app.core.responses import SuccessResponse
from app.models.medical_case import DoctorCaseStatus
from app.schemas.doctor_case import DoctorCasePreviewItem
from app.services.doctor_cases import accept_doctor_case, decline_doctor_case, list_doctor_cases

router = APIRouter(prefix="/doctors/cases", tags=["doctor-cases"])


def _serialize(row: tuple) -> DoctorCasePreviewItem:
	case_id, _patient_id, title, first_name, last_name, avatar_url, case_status, created_at, updated_at = row
	patient_name = " ".join(part for part in (first_name, last_name) if part) or None
	return DoctorCasePreviewItem(
		case_id=case_id,
		patient_name=patient_name,
		patient_avatar_url=avatar_url,
		requested_review=title,
		status=case_status,
		received_at=created_at,
		updated_at=updated_at,
	)


@router.get("/requests", response_model=SuccessResponse[list[DoctorCasePreviewItem]])
async def list_requests(
	current_doctor: ApprovedDoctor,
	case_repo: MedicalCaseRepo,
	preview: bool = Query(False),
	offset: int = Query(0, ge=0),
	limit: int = Query(50, ge=1, le=100),
) -> SuccessResponse[list[DoctorCasePreviewItem]]:
	rows = await list_doctor_cases(
		case_repo, current_doctor.id, case_status=DoctorCaseStatus.PENDING, preview=preview, offset=offset, limit=limit
	)
	return SuccessResponse(message="OK", data=[_serialize(row) for row in rows])


@router.get("", response_model=SuccessResponse[list[DoctorCasePreviewItem]])
async def list_cases(
	current_doctor: ApprovedDoctor,
	case_repo: MedicalCaseRepo,
	status_filter: DoctorCaseStatus = Query(DoctorCaseStatus.ACCEPTED, alias="status"),
	preview: bool = Query(False),
	offset: int = Query(0, ge=0),
	limit: int = Query(50, ge=1, le=100),
) -> SuccessResponse[list[DoctorCasePreviewItem]]:
	rows = await list_doctor_cases(
		case_repo, current_doctor.id, case_status=status_filter, preview=preview, offset=offset, limit=limit
	)
	return SuccessResponse(message="OK", data=[_serialize(row) for row in rows])


@router.post("/{case_id}/accept", response_model=SuccessResponse[DoctorCasePreviewItem])
async def accept_case(
	case_id: UUID, current_doctor: ApprovedDoctor, case_repo: MedicalCaseRepo
) -> SuccessResponse[DoctorCasePreviewItem]:
	case = await accept_doctor_case(case_repo, case_id, current_doctor.id)
	return SuccessResponse(
		message="Case accepted.",
		data=DoctorCasePreviewItem(
			case_id=case.id, status=case.doctor_case_status, received_at=case.created_at, updated_at=case.updated_at
		),
	)


@router.post("/{case_id}/decline", response_model=SuccessResponse[DoctorCasePreviewItem])
async def decline_case(
	case_id: UUID, current_doctor: ApprovedDoctor, case_repo: MedicalCaseRepo
) -> SuccessResponse[DoctorCasePreviewItem]:
	case = await decline_doctor_case(case_repo, case_id, current_doctor.id)
	return SuccessResponse(
		message="Case declined.",
		data=DoctorCasePreviewItem(
			case_id=case.id, status=case.doctor_case_status, received_at=case.created_at, updated_at=case.updated_at
		),
	)
