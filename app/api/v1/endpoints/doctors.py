import logging

from fastapi import APIRouter, status
from sqlalchemy import select

from app.api.deps import CurrentUser, DBSession
from app.core.exceptions import ForbiddenError
from app.core.responses import SuccessResponse
from app.models.doctor_verification import DoctorVerification, DoctorVerificationStatus
from app.schemas.user import DashboardSummary, UserMeResponse

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/doctors", tags=["doctors"])


@router.get("/dashboard", response_model=SuccessResponse[UserMeResponse], status_code=status.HTTP_200_OK)
async def get_doctor_dashboard(
	current_user: CurrentUser,
	session: DBSession,
) -> SuccessResponse[UserMeResponse]:
	"""Return Doctor Dashboard Overview data.

	Authoritative source of truth: Enforces that the user is fully email-verified
	and has an APPROVED Doctor verification status (returning 403 Forbidden otherwise).
	"""
	stmt = select(DoctorVerification).where(DoctorVerification.user_id == current_user.id)
	res = await session.execute(stmt)
	verification = res.scalar_one_or_none()

	if verification is None or verification.status != DoctorVerificationStatus.APPROVED:
		raise ForbiddenError("Access restricted. Doctor verification must be approved.")

	dashboard_payload = UserMeResponse(
		id=current_user.id,
		email=current_user.email,
		first_name=current_user.first_name,
		last_name=current_user.last_name,
		role=current_user.role,
		is_email_verified=current_user.is_email_verified,
		is_active=current_user.is_active,
		avatar_url=current_user.avatar_url,
		specialty=verification.specialty,
		verification_status=verification.status.value,
		rejection_reason=None,
		dashboard=DashboardSummary(
			total_cases=0,
			pending_reviews=0,
			completed_reviews=0,
		),
		created_at=current_user.created_at,
		last_login_at=current_user.last_login_at,
	)

	return SuccessResponse(message="Doctor dashboard data retrieved successfully", data=dashboard_payload)
