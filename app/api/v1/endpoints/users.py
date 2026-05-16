import logging
from datetime import datetime, timezone
from typing import Annotated

from fastapi import APIRouter, Depends, status
from fastapi.security import HTTPAuthorizationCredentials

from app.api.deps import CurrentUser, OtpRepo, TokenBlocklistRepo, UserRepo, bearer_scheme
from app.core.responses import SuccessResponse
from app.models.otp import OtpPurpose
from app.schemas.user import (
	EmailUpdateRequest,
	EmailUpdateVerifyRequest,
	PasswordUpdateRequest,
	ProfileUpdateRequest,
	UserResponse,
)
from app.services.auth import (
	delete_account,
	start_email_change,
	update_password,
	update_profile,
	verify_email_change,
)
from app.services.auth.tokens import decode_access_token
from app.tasks.emails import send_otp_email_task

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


@router.patch("/me", response_model=SuccessResponse[UserResponse], status_code=status.HTTP_200_OK)
async def update_profile_endpoint(
	payload: ProfileUpdateRequest,
	current_user: CurrentUser,
	user_repo: UserRepo,
) -> SuccessResponse[UserResponse]:
	"""Update the authenticated user's first and/or last name."""
	updated_user = await update_profile(
		user_repo,
		user=current_user,
		first_name=payload.first_name,
		last_name=payload.last_name,
	)
	return SuccessResponse(message="Profile updated successfully", data=UserResponse.model_validate(updated_user))


@router.patch("/me/password", response_model=SuccessResponse, status_code=status.HTTP_200_OK)
async def update_password_endpoint(
	payload: PasswordUpdateRequest,
	current_user: CurrentUser,
	user_repo: UserRepo,
) -> SuccessResponse:
	"""Change the authenticated user's password.

	Returns 400 if the current password does not match.
	"""
	await update_password(
		user_repo,
		user=current_user,
		current_password=payload.current_password,
		new_password=payload.new_password,
	)
	return SuccessResponse(message="Password updated successfully")


@router.delete("/me", response_model=SuccessResponse, status_code=status.HTTP_200_OK)
async def delete_account_endpoint(
	current_user: CurrentUser,
	user_repo: UserRepo,
	blocklist_repo: TokenBlocklistRepo,
	credentials: Annotated[HTTPAuthorizationCredentials, Depends(bearer_scheme)],
) -> SuccessResponse:
	"""Permanently delete the authenticated user's account.

	The current access token is blocklisted before deletion so any in-flight
	request using the same token is rejected immediately.
	"""
	access_token_payload = decode_access_token(credentials.credentials)
	jti: str = access_token_payload["jti"]
	expires_at = datetime.fromtimestamp(access_token_payload["exp"], tz=timezone.utc)

	await delete_account(
		user_repo,
		blocklist_repo,
		user=current_user,
		jti=jti,
		expires_at=expires_at,
	)
	return SuccessResponse(message="Account deleted successfully")
