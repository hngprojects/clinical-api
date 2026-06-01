import logging
from typing import Annotated

from fastapi import APIRouter, Cookie, Depends, status
from fastapi.security import HTTPAuthorizationCredentials

from app.api.deps import AuthSessionManagerDep, CurrentUser, OtpRepo, TokenBlocklistRepo, UserRepo, bearer_scheme
from app.core.config import get_settings
from app.core.rate_limit import enforce_action_rate_limit
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
	settings = get_settings()
	await enforce_action_rate_limit(
		key=f"rl:email-update-request:{current_user.id}",
		limit=settings.EMAIL_UPDATE_REQUEST_RATE_LIMIT,
		window_seconds=settings.EMAIL_UPDATE_REQUEST_RATE_WINDOW_SECONDS,
		message="Too many email update requests. Try again later.",
	)
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
	settings = get_settings()
	await enforce_action_rate_limit(
		key=f"rl:email-update-verify:{current_user.id}",
		limit=settings.EMAIL_UPDATE_VERIFY_RATE_LIMIT,
		window_seconds=settings.EMAIL_UPDATE_VERIFY_RATE_WINDOW_SECONDS,
		message="Too many email verification attempts. Try again later.",
	)
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
	await user_repo.commit()
	await user_repo.refresh(updated_user)
	return SuccessResponse(message="Profile updated successfully", data=UserResponse.model_validate(updated_user))


@router.patch("/me/password", response_model=SuccessResponse, status_code=status.HTTP_200_OK)
async def update_password_endpoint(
	payload: PasswordUpdateRequest,
	current_user: CurrentUser,
	user_repo: UserRepo,
	blocklist_repo: TokenBlocklistRepo,
	auth_manager: AuthSessionManagerDep,
	credentials: Annotated[HTTPAuthorizationCredentials, Depends(bearer_scheme)],
	refresh_token: Annotated[str | None, Cookie()] = None,
) -> SuccessResponse:
	"""Change the authenticated user's password.

	Returns 400 if the current password does not match. Revokes all device
	sessions (refresh tokens in auth_sessions) and blocklists the access token
	on this request and the refresh cookie when provided. Other outstanding
	access JWTs from other devices may remain valid until they expire.
	"""
	await update_password(
		user_repo,
		blocklist_repo,
		auth_manager,
		user=current_user,
		current_password=payload.current_password,
		new_password=payload.new_password,
		access_token=credentials.credentials,
		refresh_token=refresh_token,
	)
	await user_repo.commit()
	return SuccessResponse(message="Password updated successfully")


@router.delete("/me", response_model=SuccessResponse, status_code=status.HTTP_200_OK)
async def delete_account_endpoint(
	current_user: CurrentUser,
	user_repo: UserRepo,
	blocklist_repo: TokenBlocklistRepo,
	auth_manager: AuthSessionManagerDep,
	credentials: Annotated[HTTPAuthorizationCredentials, Depends(bearer_scheme)],
	refresh_token: Annotated[str | None, Cookie()] = None,
) -> SuccessResponse:
	"""Permanently delete the authenticated user's account.

	Tokens are revoked and the user row is removed in a single atomic commit.
	"""
	await delete_account(
		user_repo,
		blocklist_repo,
		auth_manager,
		user=current_user,
		access_token=credentials.credentials,
		refresh_token=refresh_token,
	)
	await user_repo.commit()
	return SuccessResponse(message="Account deleted successfully")
