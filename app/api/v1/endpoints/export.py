from uuid import UUID

from fastapi import APIRouter
from fastapi.responses import Response

from app.api.deps import AIInterpretationRepo, GuestSessionId, LabResultRepo, MedicalCaseRepo, OptionalUser
from app.core.exceptions import ForbiddenError, NotFoundError
from app.services.guest import resolve_guest_session_id
from app.services.pdf_export import generate_pdf

router = APIRouter(prefix="/cases", tags=["export"])


@router.get("/{case_id}/export")
async def export_case_pdf(
	case_id: UUID,
	current_user: OptionalUser,
	guest_session_id: GuestSessionId,
	case_repo: MedicalCaseRepo,
	interp_repo: AIInterpretationRepo,
	lab_repo: LabResultRepo,
) -> Response:
	"""
	Export a PDF summary of the AI interpretation for a medical case.

	- Enforces ownership (authenticated user or guest session).
	- Returns 404 if no completed interpretation exists.
	- Returns a downloadable PDF file.
	"""
	case = await case_repo.get_by_id(case_id)
	if case is None:
		raise NotFoundError("Medical case not found.")

	if current_user is not None:
		if case.user_id != current_user.id:
			raise ForbiddenError("You do not have access to this medical case.")
	else:
		valid_guest_id = await resolve_guest_session_id(guest_session_id)
		if case.guest_session_id != valid_guest_id:
			raise ForbiddenError("You do not have access to this medical case.")

	lab_results = await lab_repo.list_by_case(case_id, limit=1)
	if not lab_results:
		raise NotFoundError("Lab result not found for this case.")
	lab_result = lab_results[0]

	interpretation = await interp_repo.get_latest_completed_for_case(case_id)
	if interpretation is None:
		raise NotFoundError("No completed AI interpretation found for this case.")

	pdf_bytes = generate_pdf(
		case=case,
		lab_result=lab_result,
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
