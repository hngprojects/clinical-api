import logging
from datetime import datetime, timezone
from typing import Annotated
from urllib.parse import urlencode
from uuid import UUID

from fastapi import APIRouter, Cookie, Depends, HTTPException, Query, Request, status
from fastapi.responses import RedirectResponse, Response
from fastapi.security import HTTPAuthorizationCredentials

from app.api.deps import (
	AuthSessionManagerDep,
	ChatRepo,
	ClientIpHash,
	CurrentUser,
	DBSession,
	GuestSessionId,
	MedicalCaseRepo,
	OtpRepo,
	PasswordResetRepo,
	TokenBlocklistRepo,
	UserRepo,
	bearer_scheme,
)
from app.core.config import get_settings
from app.core.exceptions import BadRequestError, ForbiddenError, NotFoundError, UnauthorizedError
from app.core.rate_limit import (
	assert_login_not_rate_limited,
	assert_otp_not_locked,
	clear_login_failures,
	clear_otp_failures,
	enforce_action_rate_limit,
	enforce_rate_limit,
	record_login_failure,
	record_otp_failure,
	record_verify_otp_failure,
)
from app.core.responses import SuccessResponse
from app.core.security import hash_opaque_token
from app.models.otp import OtpPurpose
from app.models.user import UserRole
from app.schemas.auth import (
	AuthSessionResponse,
	ForgotPasswordRequest,
	LoginRequest,
	OtpDispatchResponse,
	RefreshRequest,
	ResendOtpRequest,
	ResetPasswordRequest,
	ResetTokenResponse,
	SignupRequest,
	TokenResponse,
	VerifyOtpRequest,
	VerifyResetOtpRequest,
)
from app.schemas.user import UserResponse
from app.services.auth import (
	authenticate_credentials,
	create_otp_for_user,
	create_password_reset,
	decode_access_token,
	decode_refresh_token,
	otp_ttl_seconds,
	reset_password,
	revoke_refresh_token,
	signup_user,
	verify_otp_for_user,
)
from app.services.auth.blocklist import is_token_revoked, revoke_token
from app.services.auth.otp import OtpVerificationError
from app.services.auth_sessions import AuthSessionIssue
from app.services.guest import migrate_guest_session_to_user
from app.services.oauth import (
	exchange_google_code,
	fetch_google_user_info,
	get_or_create_google_user,
)
from app.services.oauth_state import build_redirect_url, create_oauth_state, decode_oauth_state
from app.tasks.emails import send_otp_email_task

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/auth", tags=["auth"])

# Roles that may be self-provisioned through OAuth.
# Admin accounts must be created through a privileged back-office flow only.
_OAUTH_ALLOWED_ROLES: frozenset[UserRole] = frozenset({UserRole.PATIENT, UserRole.DOCTOR})


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
async def signup(
	payload: SignupRequest,
	user_repo: UserRepo,
	otp_repo: OtpRepo,
	ip_hash: ClientIpHash,
) -> SuccessResponse[OtpDispatchResponse]:
	"""Register a new user and send a 6-digit OTP for email verification."""
	settings = get_settings()
	await enforce_rate_limit(
		key=f"rl:signup:{ip_hash}",
		limit=settings.SIGNUP_RATE_LIMIT,
		window_seconds=settings.SIGNUP_RATE_WINDOW_SECONDS,
	)
	user, code = await signup_user(user_repo, otp_repo, payload)
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
		logger.exception("Failed to enqueue OTP email for %s", _mask_email(user.email))
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
async def login(
	payload: LoginRequest,
	request: Request,
	user_repo: UserRepo,
	auth_manager: AuthSessionManagerDep,
	ip_hash: ClientIpHash,
	response: Response,
) -> SuccessResponse[TokenResponse]:
	"""Authenticate with email + password. Returns a JWT on success.

	The account must have a verified email before login is permitted.
	"""
	await assert_login_not_rate_limited(ip_hash)
	try:
		user = await authenticate_credentials(
			user_repo, email=payload.email, password=payload.password, expected_role=UserRole.PATIENT
		)
	except (UnauthorizedError, NotFoundError, ForbiddenError):
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


# OTP verification & resend
@router.post(
	"/verify-otp",
	response_model=SuccessResponse[TokenResponse | ResetTokenResponse],
)
async def verify_otp(
	payload: VerifyOtpRequest,
	request: Request,
	guest_session_id: GuestSessionId,
	user_repo: UserRepo,
	otp_repo: OtpRepo,
	case_repo: MedicalCaseRepo,
	chat_repo: ChatRepo,
	auth_manager: AuthSessionManagerDep,
	ip_hash: ClientIpHash,
	response: Response,
) -> SuccessResponse:
	"""Verify the email-verification or password-reset OTP."""
	settings = get_settings()

	# Enforce rate limit per email lockout first
	await assert_otp_not_locked(payload.email)

	# Log the attempt (never log OTP value)
	logger.info("OTP verification attempt: email=%s, purpose=%s", _mask_email(payload.email), payload.purpose)

	await enforce_action_rate_limit(
		key=f"rl:verify-otp:{ip_hash}",
		limit=settings.OTP_FAILURE_RATE_LIMIT,
		window_seconds=settings.OTP_FAILURE_RATE_WINDOW_SECONDS,
		message="Too many OTP verification attempts. Try again later.",
	)

	user = await user_repo.get_by_email(payload.email.strip().lower(), role=payload.role)
	if user is None:
		await record_verify_otp_failure(ip_hash)
		await record_otp_failure(payload.email)
		raise BadRequestError("The code you entered is incorrect.")

	purpose_enum = OtpPurpose.RESET_PASSWORD if payload.purpose == "reset_password" else OtpPurpose.EMAIL_VERIFICATION

	try:
		await verify_otp_for_user(
			otp_repo,
			user_id=user.id,
			purpose=purpose_enum,
			code=payload.code,
			user_email=payload.email,
		)
	except OtpVerificationError as exc:
		await user_repo.commit()
		await record_verify_otp_failure(ip_hash)
		await record_otp_failure(payload.email)
		raise BadRequestError(str(exc))

	# Clear failures upon successful verification
	await clear_otp_failures(payload.email)

	if purpose_enum == OtpPurpose.RESET_PASSWORD:
		settings = get_settings()
		reset_repo = PasswordResetRepo(user_repo._session)
		raw = await create_password_reset(
			reset_repo, user, expires_minutes=settings.PASSWORD_RESET_TOKEN_EXPIRES_MINUTES
		)
		await user_repo.commit()
		return SuccessResponse(
			message="Reset token issued.",
			data=ResetTokenResponse(
				reset_token=raw,
				expires_in_seconds=settings.PASSWORD_RESET_TOKEN_EXPIRES_MINUTES * 60,
			),
		)

	# Email verification flow
	user.is_email_verified = True
	user.last_login_at = datetime.now(timezone.utc)
	await user_repo.commit()

	migration_guest_id = payload.guest_session_id or guest_session_id
	if migration_guest_id:
		await migrate_guest_session_to_user(
			case_repo,
			chat_repo,
			guest_session_id=migration_guest_id,
			user_id=user.id,
		)
	issue = await auth_manager.create(
		user.id,
		payload.device_id,
		platform=payload.platform,
		ip_hash=ip_hash,
		user_agent=request.headers.get("user-agent"),
	)
	_set_refresh_cookie(response, issue.refresh_token)
	return SuccessResponse(
		message="Email verified. Welcome!",
		data=_token_response(issue, user=UserResponse.model_validate(user)),
	)


# TODO: Move to service layer and add rate-limiting to prevent abuse
@router.post(
	"/resend-otp",
	response_model=SuccessResponse[OtpDispatchResponse],
)
async def resend(
	payload: ResendOtpRequest,
	user_repo: UserRepo,
	otp_repo: OtpRepo,
	ip_hash: ClientIpHash,
) -> SuccessResponse[OtpDispatchResponse]:
	"""Re-send the OTP (email-verification or password-reset)."""
	settings = get_settings()

	# Log the attempt (never log OTP value)
	normalized_email = payload.email.strip().lower()
	logger.info("OTP resend request: email=%s", _mask_email(normalized_email))

	await enforce_action_rate_limit(
		key=f"rl:resend-otp:{ip_hash}",
		limit=settings.RESEND_OTP_RATE_LIMIT,
		window_seconds=settings.RESEND_OTP_RATE_WINDOW_SECONDS,
		message="Too many OTP resend requests. Try again later.",
	)

	# Rate limit resend per email
	await enforce_action_rate_limit(
		key=f"rl:resend-otp:{normalized_email}",
		limit=settings.RESEND_OTP_RATE_LIMIT,
		window_seconds=settings.RESEND_OTP_RATE_WINDOW_SECONDS,
		message="Too many OTP resend requests. Try again later.",
	)

	user = await user_repo.get_by_email(normalized_email, role=payload.role)
	if not user or not user.is_active:
		# Return the same success response to prevent account enumeration
		return SuccessResponse(
			message="A new code has been sent to your email.",
			data=OtpDispatchResponse(
				email=normalized_email,
				expires_in_seconds=otp_ttl_seconds(),
			),
		)

	# Determine OTP purpose
	latest_otp = await otp_repo.get_latest_active(user_id=user.id, purpose=OtpPurpose.RESET_PASSWORD)
	if latest_otp:
		purpose = OtpPurpose.RESET_PASSWORD
	else:
		latest_otp = await otp_repo.get_latest_active(user_id=user.id, purpose=OtpPurpose.EMAIL_VERIFICATION)
		if latest_otp:
			purpose = OtpPurpose.EMAIL_VERIFICATION
		else:
			purpose = OtpPurpose.RESET_PASSWORD if user.is_email_verified else OtpPurpose.EMAIL_VERIFICATION

	if purpose == OtpPurpose.EMAIL_VERIFICATION and user.is_email_verified:
		# Return success directly
		return SuccessResponse(
			message="A new code has been sent to your email.",
			data=OtpDispatchResponse(
				email=normalized_email,
				expires_in_seconds=otp_ttl_seconds(),
			),
		)

	_, code = await create_otp_for_user(otp_repo, user_id=user.id, purpose=purpose)
	await user_repo.commit()

	email_dispatched = False
	try:
		send_otp_email_task.delay(
			to_email=user.email,
			first_name=user.first_name or user.email.split("@")[0],
			code=code,
			purpose=purpose.value,
		)
		email_dispatched = True
	except Exception:
		logger.exception("Failed to enqueue OTP email for %s", _mask_email(user.email))

	return SuccessResponse(
		message=(
			"A new code has been sent to your email."
			if email_dispatched
			else "A new code was created. If you do not receive an email, request another code."
		),
		data=OtpDispatchResponse(
			email=normalized_email,
			expires_in_seconds=otp_ttl_seconds(),
		),
	)


# Current user
@router.get(
	"/me",
	response_model=SuccessResponse[UserResponse],
)
async def me(current_user: CurrentUser) -> SuccessResponse[UserResponse]:
	"""Return the currently authenticated user."""
	return SuccessResponse(
		message="OK",
		data=UserResponse.model_validate(current_user),
	)


# Password reset
@router.post("/forgot-password", response_model=SuccessResponse)
async def forgot_password(
	payload: ForgotPasswordRequest,
	http_request: Request,
	user_repo: UserRepo,
	otp_repo: OtpRepo,
	session: DBSession,
	ip_hash: ClientIpHash,
) -> SuccessResponse:
	"""Send an OTP to the user's email to allow password reset.

	Always returns 200 regardless of whether the email is registered to prevent
	user-enumeration attacks.
	"""
	settings = get_settings()

	await enforce_action_rate_limit(
		key=f"rl:forgot-password:{ip_hash}",
		limit=settings.FORGOT_PASSWORD_RATE_LIMIT,
		window_seconds=settings.FORGOT_PASSWORD_RATE_WINDOW_SECONDS,
		message="Too many password reset requests. Try again later.",
	)

	user = await user_repo.get_by_email(payload.email.strip().lower(), payload.role)
	if user:
		_, code = await create_otp_for_user(otp_repo, user_id=user.id, purpose=OtpPurpose.RESET_PASSWORD)
		await session.commit()
		try:
			send_otp_email_task.delay(to_email=user.email, code=code, purpose=OtpPurpose.RESET_PASSWORD.value)
		except Exception:
			logger.exception("Failed to enqueue password reset OTP for %s", _mask_email(user.email))
	return SuccessResponse(message="If this email is registered, you'll receive a reset code shortly.")


@router.post("/verify-reset-otp", response_model=SuccessResponse[ResetTokenResponse])
async def verify_reset_otp(
	request: VerifyResetOtpRequest,
	user_repo: UserRepo,
	otp_repo: OtpRepo,
	reset_repo: PasswordResetRepo,
	session: DBSession,
) -> SuccessResponse[ResetTokenResponse]:
	"""Verify a password-reset OTP and issue an opaque reset token for final password change."""
	user = await user_repo.get_by_email(request.email.strip().lower(), request.role)
	if not user:
		raise UnauthorizedError("Invalid or expired reset OTP")

	try:
		await verify_otp_for_user(otp_repo, user_id=user.id, purpose=OtpPurpose.RESET_PASSWORD, code=request.code)
	except OtpVerificationError:
		await session.commit()
		raise UnauthorizedError("Invalid or expired reset OTP")

	settings = get_settings()
	raw = await create_password_reset(reset_repo, user, expires_minutes=settings.PASSWORD_RESET_TOKEN_EXPIRES_MINUTES)
	await session.commit()
	return SuccessResponse(
		message="Reset token issued.",
		data=ResetTokenResponse(reset_token=raw, expires_in_seconds=settings.PASSWORD_RESET_TOKEN_EXPIRES_MINUTES * 60),
	)


@router.post(
	"/reset-password",
	response_model=SuccessResponse,
)
async def password_reset(
	request: ResetPasswordRequest,
	user_repo: UserRepo,
	reset_repo: PasswordResetRepo,
	session: DBSession,
	ip_hash: ClientIpHash,
) -> SuccessResponse:
	"""Reset password using an opaque reset token previously issued after OTP verification."""
	await enforce_action_rate_limit(
		key=f"rl:reset-password:ip:{ip_hash}",
		limit=5,
		window_seconds=600,
		message="Too many password reset attempts. Try again later.",
	)
	try:
		await reset_password(reset_repo, user_repo, raw_token=request.token, new_password=request.new_password)
	except UnauthorizedError:
		raise UnauthorizedError("Invalid or expired reset token")

	await session.commit()
	return SuccessResponse(message="Password reset successfully.")


# Logout
@router.post(
	"/logout",
	response_model=SuccessResponse,
	status_code=status.HTTP_200_OK,
)
async def logout(
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


# Google OAuth
@router.get("/google")
async def google_login(
	guest_session_id: str | None = Query(None, description="Guest session to migrate after OAuth"),
	device_id: str | None = Query(None, description="Client device identifier for per-device auth session"),
	platform: str | None = Query("web", description="Client platform (web, ios, android)"),
	return_url: str | None = Query(None, description="Mobile deep link to redirect after auth"),
	role: UserRole = Query(UserRole.PATIENT, description="Desired role of the user (patient or doctor)"),
) -> RedirectResponse:
	"""Redirect to Google's OAuth consent screen."""
	if role not in _OAUTH_ALLOWED_ROLES:
		raise HTTPException(status_code=400, detail=f"Role '{role.value}' cannot be provisioned through OAuth.")
	settings = get_settings()
	oauth_state = create_oauth_state(
		guest_session_id=guest_session_id,
		device_id=device_id,
		platform=platform,
		return_url=return_url,
		role=role,
	)
	query_params = urlencode(
		{
			"client_id": settings.GOOGLE_CLIENT_ID,
			"redirect_uri": settings.GOOGLE_REDIRECT_URI,
			"response_type": "code",
			"scope": "openid email profile",
			"state": oauth_state,
		}
	)
	google_auth_url = f"https://accounts.google.com/o/oauth2/v2/auth?{query_params}"
	return RedirectResponse(url=google_auth_url)


@router.get("/google/callback")
async def google_callback(
	code: str,
	request: Request,
	user_repo: UserRepo,
	case_repo: MedicalCaseRepo,
	chat_repo: ChatRepo,
	auth_manager: AuthSessionManagerDep,
	ip_hash: ClientIpHash,
	response: Response,
	state: str = "",
) -> RedirectResponse:
	"""Handle the Google OAuth callback and redirect to the frontend with app tokens."""
	token_data = await exchange_google_code(code)
	google_access_token = token_data.get("access_token")
	if not google_access_token:
		raise UnauthorizedError("Google access token not found")

	google_user = await fetch_google_user_info(google_access_token)

	oauth_ctx = decode_oauth_state(state)
	# Clamp to the allowlist — guards against a tampered/upgraded state token
	# that somehow encodes a privileged role (e.g. admin).
	raw_role = oauth_ctx.role if oauth_ctx and oauth_ctx.role else UserRole.PATIENT
	role = raw_role if raw_role in _OAUTH_ALLOWED_ROLES else UserRole.PATIENT

	user = await get_or_create_google_user(user_repo, google_user, role=role)
	guest_id = oauth_ctx.guest_session_id if oauth_ctx else None
	if guest_id:
		await migrate_guest_session_to_user(
			case_repo,
			chat_repo,
			guest_session_id=guest_id,
			user_id=user.id,
		)

	if oauth_ctx and oauth_ctx.device_id:
		oauth_device_id = oauth_ctx.device_id
	elif oauth_ctx and oauth_ctx.guest_session_id:
		oauth_device_id = f"guest-{oauth_ctx.guest_session_id[:8]}"
	else:
		ua = request.headers.get("user-agent", "unknown")
		oauth_device_id = f"google-{hash_opaque_token(ua)[:16]}"

	oauth_platform = oauth_ctx.platform if oauth_ctx and oauth_ctx.platform else "web"

	issue = await auth_manager.create(
		user.id,
		oauth_device_id,
		platform=oauth_platform,
		ip_hash=ip_hash,
		user_agent=request.headers.get("user-agent"),
	)
	_set_refresh_cookie(response, issue.refresh_token)
	app_access_token = issue.access_token

	settings = get_settings()
	base_redirect = oauth_ctx.return_url if oauth_ctx and oauth_ctx.return_url else settings.FRONTEND_AUTH_CALLBACK_URL
	redirect_url = build_redirect_url(
		base_redirect,
		app_access_token,
		refresh_token=issue.refresh_token,
	)
	return RedirectResponse(url=redirect_url)


# Token refresh
@router.post("/refresh", response_model=SuccessResponse[TokenResponse])
async def refresh(
	auth_manager: AuthSessionManagerDep,
	blocklist_repo: TokenBlocklistRepo,
	response: Response,
	payload: RefreshRequest | None = None,
	refresh_token_cookie: Annotated[str | None, Cookie(alias="refresh_token")] = None,
) -> SuccessResponse[TokenResponse]:
	"""Refresh the access and refresh tokens.

	Accepts the refresh token from the HttpOnly cookie (web) or request body
	(mobile). Validates against auth_sessions, blocklists the old refresh JWT,
	then rotates to a new access/refresh pair on the same device.
	"""
	refresh_token = refresh_token_cookie or (payload.refresh_token if payload else None)
	if not refresh_token:
		raise UnauthorizedError("Refresh token is required")
	payload_decoded = decode_refresh_token(refresh_token)
	refresh_token_jti: str = payload_decoded["jti"]
	if await is_token_revoked(blocklist_repo, refresh_token_jti):
		raise UnauthorizedError("Refresh token has been revoked")
	await revoke_refresh_token(refresh_token, blocklist_repo)
	issue = await auth_manager.rotate_refresh(refresh_token)

	_set_refresh_cookie(response, issue.refresh_token)
	return SuccessResponse(
		message="Tokens refreshed",
		data=_token_response(issue),
	)


@router.get(
	"/sessions",
	response_model=SuccessResponse[list[AuthSessionResponse]],
)
async def list_sessions(
	current_user: CurrentUser,
	auth_manager: AuthSessionManagerDep,
) -> SuccessResponse[list[AuthSessionResponse]]:
	"""List active device sessions for the authenticated user."""
	sessions = await auth_manager.list_sessions(current_user.id)
	return SuccessResponse(
		message="OK",
		data=[AuthSessionResponse.from_session(s) for s in sessions],
	)


@router.delete(
	"/sessions/{session_id}",
	response_model=SuccessResponse,
)
async def revoke_session(
	session_id: UUID,
	current_user: CurrentUser,
	auth_manager: AuthSessionManagerDep,
) -> SuccessResponse:
	"""Revoke a single device session."""
	await auth_manager.revoke(session_id, current_user.id)
	return SuccessResponse(message="Session revoked.")


@router.delete(
	"/sessions",
	response_model=SuccessResponse,
)
async def revoke_all_sessions(
	current_user: CurrentUser,
	auth_manager: AuthSessionManagerDep,
	blocklist_repo: TokenBlocklistRepo,
	credentials: Annotated[HTTPAuthorizationCredentials, Depends(bearer_scheme)],
	refresh_token: Annotated[str | None, Cookie()] = None,
) -> SuccessResponse:
	"""Revoke every device session for the current user."""
	count = await auth_manager.revoke_all(current_user.id)
	if refresh_token:
		await auth_manager.revoke_by_refresh_token(refresh_token)
		await revoke_refresh_token(refresh_token, blocklist_repo)
	if credentials is not None and credentials.scheme.lower() == "bearer":
		payload = decode_access_token(credentials.credentials)
		await revoke_token(
			blocklist_repo,
			jti=payload["jti"],
			user_id=current_user.id,
			expires_at=datetime.fromtimestamp(payload["exp"], tz=timezone.utc),
		)
	return SuccessResponse(message=f"Revoked {count} session(s).")
