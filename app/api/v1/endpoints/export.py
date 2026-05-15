from uuid import UUID

from fastapi import APIRouter
from fastapi.responses import Response

from app.api.deps import AIInterpretationRepo, GuestSessionId, MedicalCaseRepo, OptionalUser
from app.core.exceptions import NotFoundError
from app.services.ai_interpretation import get_latest_for_case
from app.services.medical_case import get_case
from app.services.pdf_export import generate_pdf

router = APIRouter(prefix="/cases", tags=["export"])


@router.get("/{case_id}/export")
async def export_case_pdf(
	case_id: UUID,
	current_user: OptionalUser,
	guest_session_id: GuestSessionId,
	case_repo: MedicalCaseRepo,
	interp_repo: AIInterpretationRepo,
) -> Response:
	"""
	Export a PDF summary of the AI interpretation for a medical case.

	- Enforces ownership (authenticated user or guest session).
	- Returns 404 if no completed interpretation exists.
	- Returns a downloadable PDF file.
	"""
	case = await get_case(
		case_repo,
		case_id,
		user=current_user,
		guest_session_id=guest_session_id,
	)

	interpretation = await get_latest_for_case(interp_repo, case_id)
	if interpretation is None:
		raise NotFoundError("No completed AI interpretation found for this case.")

	pdf_bytes = generate_pdf(
		case=case,
		interpretation=interpretation,
		user=current_user,
	)

	filename = f"clinsight-report-{case_id}.pdf"

	return Response(
		content=pdf_bytes,
		media_type="application/pdf",
		headers={
			"Content-Disposition": f'attachment; filename="{filename}"',
			"Content-Length": str(len(pdf_bytes)),
		},
	)