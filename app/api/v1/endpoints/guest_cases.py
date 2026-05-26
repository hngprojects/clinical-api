from fastapi import APIRouter, Query

from app.api.deps import CurrentGuestSessionDep, GuestSessionManagerDep, LabResultRepo, MedicalCaseRepo
from app.core.responses import SuccessResponse
from app.schemas.medical_case import MedicalCaseResponse
from app.services.medical_case import get_case_titles, list_cases_for_guest_session

router = APIRouter(prefix="/guest/cases", tags=["guest-sessions"])


@router.get(
	"",
	response_model=SuccessResponse[list[MedicalCaseResponse]],
)
async def list_guest_cases(
	guest_session: CurrentGuestSessionDep,
	manager: GuestSessionManagerDep,
	case_repo: MedicalCaseRepo,
	lab_repo: LabResultRepo,
	offset: int = Query(0, ge=0),
	limit: int = Query(50, ge=1, le=100),
) -> SuccessResponse[list[MedicalCaseResponse]]:
	"""List medical cases for the current guest session (pre-signup history)."""
	cases, _total = await list_cases_for_guest_session(
		case_repo,
		guest_session,
		offset=offset,
		limit=limit,
		manager=manager,
	)
	titles = await get_case_titles(cases, lab_repo)
	return SuccessResponse(
		message="OK",
		data=[
			MedicalCaseResponse.model_validate(c).model_copy(update={"title": titles.get(c.id)})
			for c in cases
		],
	)
