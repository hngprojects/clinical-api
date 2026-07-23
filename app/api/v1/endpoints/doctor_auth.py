import logging
from datetime import datetime, timezone
from typing import Annotated

from fastapi import APIRouter, Cookie, Depends, Request, status
from fastapi.responses import Response
from fastapi.security import HTTPAuthorizationCredentials

from app.api.deps import (
	AuthSessionManagerDep,
	ClientIpHash,
	CurrentUser,
	OtpRepo,
	TokenBlocklistRepo,
	UserRepo,
	bearer_scheme,
)
from app.core.config import get_settings
from app.core.exceptions import ForbiddenError, NotFoundError, UnauthorizedError
from app.core.rate_limit import (
	assert_login_not_rate_limited,
	clear_login_failures,
	enforce_rate_limit,
	record_login_failure,
)
from app.core.responses import SuccessResponse
from app.models.otp import OtpPurpose
from app.models.user import UserRole
from app.schemas.auth import (
	LoginRequest,
	OtpDispatchResponse,
	SignupRequest,
	TokenResponse,
)
from app.schemas.user import UserResponse
from app.services.auth import (
	authenticate_credentials,
	decode_access_token,
	otp_ttl_seconds,
	revoke_refresh_token,
	signup_user,
)
from app.services.auth.blocklist import revoke_token
from app.services.auth_sessions import AuthSessionIssue
from app.tasks.emails import send_otp_email_task

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/auth/doctor", tags=["doctor-auth"])


def _mask_email(email: str) -> str:
	if "@" in email:
		local, domain = email.split("@", 1)
		return f"{local[:2]}***@{domain}"
	return "***"


def _set_refresh_cookie(response: Response, refresh_token: str) -> None:
	settings = get_settings()
	response.set_cookie(
		key="refresh_token",
		value=refresh_token,
		httponly=True,
		secure=settings.COOKIE_SECURE,
		samesite=settings.COOKIE_SAMESITE,
		max_age=settings.JWT_REFRESH_TOKEN_EXPIRES_MINUTES * 60,
	)


def _token_response(issue: AuthSessionIssue, *, user: UserResponse | None = None) -> TokenResponse:
	return TokenResponse(
		access_token=issue.access_token,
		refresh_token=issue.refresh_token,
		token_type="bearer",
		expires_in=issue.expires_in,
		user=user,
	)


# Signup
@router.post(
	"/signup",
	response_model=SuccessResponse[OtpDispatchResponse],
	status_code=status.HTTP_201_CREATED,
)
async def doctor_signup(
	payload: SignupRequest,
	user_repo: UserRepo,
	otp_repo: OtpRepo,
	ip_hash: ClientIpHash,
) -> SuccessResponse[OtpDispatchResponse]:
	"""Register a new doctor account and send a 6-digit OTP for email verification."""
	settings = get_settings()
	await enforce_rate_limit(
		key=f"rl:signup:doctor:{ip_hash}",
		limit=settings.SIGNUP_RATE_LIMIT,
		window_seconds=settings.SIGNUP_RATE_WINDOW_SECONDS,
	)
	user, code = await signup_user(user_repo, otp_repo, payload, role=UserRole.DOCTOR)
	email_dispatched = False
	try:
		send_otp_email_task.delay(
			to_email=user.email,
			first_name=user.first_name or user.email.split("@")[0],
			code=code,
			purpose=OtpPurpose.EMAIL_VERIFICATION.value,
		)
		email_dispatched = True
	except Exception:
		logger.exception("Failed to enqueue OTP email for doctor %s", _mask_email(user.email))
	return SuccessResponse(
		message=(
			"Verification code sent to your email."
			if email_dispatched
			else "Verification code created. If you do not receive an email, request a new code."
		),
		data=OtpDispatchResponse(
			email=user.email,
			expires_in_seconds=otp_ttl_seconds(),
		),
	)


# Login
@router.post(
	"/login",
	response_model=SuccessResponse[TokenResponse],
)
async def doctor_login(
	payload: LoginRequest,
	request: Request,
	user_repo: UserRepo,
	auth_manager: AuthSessionManagerDep,
	ip_hash: ClientIpHash,
	response: Response,
) -> SuccessResponse[TokenResponse]:
	"""Authenticate a doctor with email + password. Returns a JWT on success.

	The account must have a verified email and the doctor role before login is permitted.
	"""
	await assert_login_not_rate_limited(ip_hash)
	try:
		user = await authenticate_credentials(
			user_repo,
			email=payload.email,
			password=payload.password,
			expected_role=UserRole.DOCTOR,
		)
	except (NotFoundError, UnauthorizedError, ForbiddenError):
		await record_login_failure(ip_hash)
		raise
	await clear_login_failures(ip_hash)
	issue = await auth_manager.create(
		user.id,
		payload.device_id,
		platform=payload.platform,
		ip_hash=ip_hash,
		user_agent=request.headers.get("user-agent"),
	)
	_set_refresh_cookie(response, issue.refresh_token)
	return SuccessResponse(
		message="Logged in successfully.",
		data=_token_response(issue, user=UserResponse.model_validate(user)),
	)


# Logout
@router.post(
	"/logout",
	response_model=SuccessResponse,
	status_code=status.HTTP_200_OK,
)
async def doctor_logout(
	current_user: CurrentUser,
	auth_manager: AuthSessionManagerDep,
	blocklist_repo: TokenBlocklistRepo,
	credentials: Annotated[HTTPAuthorizationCredentials, Depends(bearer_scheme)],
	refresh_token: Annotated[str | None, Cookie()] = None,
) -> SuccessResponse:
	"""Revoke the current access token and refresh token.

	Both the access JWT and the refresh JWT are added to the server-side
	blocklist so they cannot be reused even if their intrinsic TTL has not yet
	elapsed.  The client is still responsible for discarding the tokens locally.
	"""
	if not refresh_token:
		raise UnauthorizedError("Refresh token cookie is required")
	access_token_payload = decode_access_token(credentials.credentials)
	access_token_jti: str = access_token_payload["jti"]
	access_token_expires_at = datetime.fromtimestamp(access_token_payload["exp"], tz=timezone.utc)
	await revoke_token(
		blocklist_repo,
		jti=access_token_jti,
		user_id=current_user.id,
		expires_at=access_token_expires_at,
	)
	await auth_manager.revoke_by_refresh_token(refresh_token)
	await revoke_refresh_token(refresh_token, blocklist_repo)
	await blocklist_repo.commit()
	return SuccessResponse(message="Logged out successfully.")
