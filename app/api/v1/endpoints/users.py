import logging

from fastapi import APIRouter, status

from app.api.deps import CurrentUser, OtpRepo, UserRepo
from app.core.responses import SuccessResponse
from app.models.otp import OtpPurpose
from app.schemas.user import EmailUpdateRequest, EmailUpdateVerifyRequest, UserResponse
from app.services.auth import start_email_change, verify_email_change
from app.tasks.email import send_otp_email_task

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/users", tags=["users"])


@router.post("/me/email", response_model=SuccessResponse, status_code=status.HTTP_200_OK)
async def request_email_update(
	payload: EmailUpdateRequest,
	current_user: CurrentUser,
	user_repo: UserRepo,
	otp_repo: OtpRepo,
) -> SuccessResponse:
	"""Begin email update by sending a verification token to the new address."""
	user, code = await start_email_change(
		user_repo,
		otp_repo,
		user=current_user,
		new_email=payload.email,
		password=payload.password,
	)

	try:
		send_otp_email_task.delay(
			to_email=user.pending_email or payload.email,
			first_name=user.first_name or user.email.split("@")[0],
			code=code,
			purpose=OtpPurpose.EMAIL_VERIFICATION.value,
		)
	except Exception:
		logger.exception("Failed to enqueue email-change OTP for user_id=%s", str(user.id))

	return SuccessResponse(message="Verification email sent to new address")


@router.post("/me/email/verify", response_model=SuccessResponse[UserResponse], status_code=status.HTTP_200_OK)
async def verify_email_update(
	payload: EmailUpdateVerifyRequest,
	current_user: CurrentUser,
	user_repo: UserRepo,
	otp_repo: OtpRepo,
) -> SuccessResponse[UserResponse]:
	"""Finalize email update if the provided token is valid."""
	updated_user = await verify_email_change(
		user_repo,
		otp_repo,
		user=current_user,
		token=payload.token,
	)
	return SuccessResponse(message="Email updated successfully", data=UserResponse.model_validate(updated_user))
