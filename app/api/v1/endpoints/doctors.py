import logging
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, status
from sqlalchemy import func, select

from app.api.deps import ApprovedDoctor, DBSession, DoctorUser
from app.core.exceptions import ForbiddenError
from app.core.responses import SuccessResponse
from app.models.doctor_verification import DoctorVerification, DoctorVerificationStatus
from app.models.medical_case import DoctorCaseStatus, MedicalCase, MedicalCaseStatus
from app.schemas.user import (
	DashboardSummary,
	DoctorDashboardStatisticsResponse,
	DoctorDutyStatusRequest,
	DoctorDutyStatusResponse,
	UserMeResponse,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/doctors", tags=["doctors"])

DUTY_DURATION_HOURS = 12


def _compute_duty_info(user) -> tuple[bool, datetime | None, int]:
	"""Compute doctor duty status enforcing the 12-hour automated system off-duty rule."""
	if not user.is_on_duty or user.on_duty_since is None:
		return False, None, 0

	now = datetime.now(timezone.utc)
	on_duty_since = user.on_duty_since
	if on_duty_since.tzinfo is None:
		on_duty_since = on_duty_since.replace(tzinfo=timezone.utc)

	elapsed_seconds = (now - on_duty_since).total_seconds()
	max_duty_seconds = DUTY_DURATION_HOURS * 3600

	if elapsed_seconds >= max_duty_seconds:
		user.is_on_duty = False
		user.on_duty_since = None
		return False, None, 0

	expires_at = on_duty_since + timedelta(hours=DUTY_DURATION_HOURS)
	remaining_seconds = int(max_duty_seconds - elapsed_seconds)
	return True, expires_at, remaining_seconds


async def _get_doctor_statistics(session: DBSession, doctor_id) -> DoctorDashboardStatisticsResponse:
	"""Aggregate activity and caseload statistics for doctor."""
	pending_stmt = (
		select(func.count())
		.select_from(MedicalCase)
		.where(MedicalCase.doctor_id == doctor_id, MedicalCase.doctor_case_status == DoctorCaseStatus.PENDING)
	)
	pending_res = await session.execute(pending_stmt)
	pending_reviews = pending_res.scalar() or 0

	accepted_stmt = (
		select(func.count())
		.select_from(MedicalCase)
		.where(MedicalCase.doctor_id == doctor_id, MedicalCase.doctor_case_status == DoctorCaseStatus.ACCEPTED)
	)
	accepted_res = await session.execute(accepted_stmt)
	accepted_cases = accepted_res.scalar() or 0

	completed_stmt = (
		select(func.count())
		.select_from(MedicalCase)
		.where(MedicalCase.doctor_id == doctor_id, MedicalCase.status == MedicalCaseStatus.COMPLETE)
	)
	completed_res = await session.execute(completed_stmt)
	completed_cases = completed_res.scalar() or 0

	total_stmt = (
		select(func.count())
		.select_from(MedicalCase)
		.where(MedicalCase.doctor_id == doctor_id)
	)
	total_res = await session.execute(total_stmt)
	total_cases = total_res.scalar() or 0

	# Earnings model: $50.00 per completed case review
	earnings = round(float(completed_cases * 50.0), 2)

	return DoctorDashboardStatisticsResponse(
		pending_reviews=pending_reviews,
		accepted_cases=accepted_cases,
		completed_cases=completed_cases,
		earnings=earnings,
		total_cases=total_cases,
	)


@router.get("/dashboard", response_model=SuccessResponse[UserMeResponse], status_code=status.HTTP_200_OK)
async def get_doctor_dashboard(
	current_user: DoctorUser,
	session: DBSession,
) -> SuccessResponse[UserMeResponse]:
	"""Return Doctor Dashboard Overview data."""
	stmt = select(DoctorVerification).where(DoctorVerification.user_id == current_user.id)
	res = await session.execute(stmt)
	verification = res.scalar_one_or_none()

	if verification is None or verification.status != DoctorVerificationStatus.APPROVED:
		raise ForbiddenError("Access restricted. Doctor verification must be approved.")

	was_on_duty = current_user.is_on_duty
	is_on_duty, expires_at, remaining_seconds = _compute_duty_info(current_user)
	if was_on_duty and not is_on_duty:
		await session.commit()

	stats = await _get_doctor_statistics(session, current_user.id)

	show_banner = (
		verification is not None
		and verification.status != DoctorVerificationStatus.APPROVED
		and not current_user.is_verification_dismissed
	)

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
		rejection_reason=verification.rejection_reason,
		is_on_duty=is_on_duty,
		on_duty_expires_at=expires_at,
		remaining_duty_seconds=remaining_seconds,
		is_verification_dismissed=current_user.is_verification_dismissed,
		show_verification_banner=show_banner,
		dashboard=DashboardSummary(
			total_cases=stats.total_cases,
			pending_reviews=stats.pending_reviews,
			accepted_cases=stats.accepted_cases,
			completed_cases=stats.completed_cases,
			earnings=stats.earnings,
		),
		created_at=current_user.created_at,
		last_login_at=current_user.last_login_at,
	)

	return SuccessResponse(message="Doctor dashboard data retrieved successfully", data=dashboard_payload)


@router.get(
	"/dashboard/statistics",
	response_model=SuccessResponse[DoctorDashboardStatisticsResponse],
	status_code=status.HTTP_200_OK,
)
async def get_doctor_dashboard_statistics(
	current_user: ApprovedDoctor,
	session: DBSession,
) -> SuccessResponse[DoctorDashboardStatisticsResponse]:
	"""Return key statistics about doctor's activity and caseload (Pending Reviews, Accepted Cases, Completed Cases, Earnings)."""
	stats = await _get_doctor_statistics(session, current_user.id)
	return SuccessResponse(message="Doctor statistics retrieved successfully.", data=stats)


@router.post(
	"/duty-status",
	response_model=SuccessResponse[DoctorDutyStatusResponse],
	status_code=status.HTTP_200_OK,
)
async def update_doctor_duty_status(
	payload: DoctorDutyStatusRequest,
	current_user: ApprovedDoctor,
	session: DBSession,
) -> SuccessResponse[DoctorDutyStatusResponse]:
	"""Update doctor duty status.

	Doctors can switch ON DUTY. Manual OFF DUTY toggling by the doctor is disabled;
	off-duty transition is automatically executed by the system after 12 hours.
	"""
	if not payload.is_on_duty:
		raise ForbiddenError(
			"Permission denied. Manual off-duty action disabled; system automatically sets off-duty status 12 hours after going on duty."
		)

	is_on_duty, expires_at, remaining_seconds = _compute_duty_info(current_user)
	if is_on_duty:
		return SuccessResponse(
			message="Doctor is already ON DUTY.",
			data=DoctorDutyStatusResponse(
				is_on_duty=True,
				on_duty_since=current_user.on_duty_since,
				on_duty_expires_at=expires_at,
				remaining_duty_seconds=remaining_seconds,
			),
		)
	now = datetime.now(timezone.utc)
	current_user.is_on_duty = True
	current_user.on_duty_since = now
	await session.commit()
	await session.refresh(current_user)

	is_on_duty, expires_at, remaining_seconds = _compute_duty_info(current_user)

	return SuccessResponse(
		message="Doctor is now ON DUTY.",
		data=DoctorDutyStatusResponse(
			is_on_duty=is_on_duty,
			on_duty_since=current_user.on_duty_since,
			on_duty_expires_at=expires_at,
			remaining_duty_seconds=remaining_seconds,
		),
	)
