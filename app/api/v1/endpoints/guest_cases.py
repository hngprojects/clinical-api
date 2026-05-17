from fastapi import APIRouter, Query

from app.api.deps import GuestSessionId, MedicalCaseRepo
from app.core.exceptions import UnauthorizedError
from app.core.responses import SuccessResponse
from app.schemas.medical_case import MedicalCaseResponse
from app.services.medical_case import list_cases_for_guest_session

router = APIRouter(prefix="/guest/cases", tags=["guest-sessions"])


@router.get(
	"",
	response_model=SuccessResponse[list[MedicalCaseResponse]],
)
async def list_guest_cases(
	guest_session_id: GuestSessionId,
	case_repo: MedicalCaseRepo,
	offset: int = Query(0, ge=0),
	limit: int = Query(50, ge=1, le=100),
) -> SuccessResponse[list[MedicalCaseResponse]]:
	"""List medical cases for the current guest session (pre-signup history)."""
	if not guest_session_id:
		raise UnauthorizedError("Missing X-Guest-Session-Id header.")

	cases, _total = await list_cases_for_guest_session(
		case_repo,
		guest_session_id,
		offset=offset,
		limit=limit,
	)
	return SuccessResponse(
		message="OK",
		data=[MedicalCaseResponse.model_validate(c) for c in cases],
	)
