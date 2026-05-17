from datetime import datetime, timezone

from sqlalchemy.exc import IntegrityError

from app.core.config import get_settings
from app.core.exceptions import (
	BadRequestError,
	ConflictError,
	ForbiddenError,
	NotFoundError,
	UnauthorizedError,
)
from app.core.security import hash_password, verify_password
from app.models.otp import OtpPurpose
from app.models.user import User, UserRole
from app.repositories.otp import OtpRepository
from app.repositories.token_blocklist import TokenBlocklistRepository
from app.repositories.user import UserRepository
from app.schemas.auth import SignupRequest
from app.services.auth.otp import (
	OtpVerificationError,
	create_otp_for_user,
	verify_otp_for_user,
)
from app.services.auth.tokens import create_access_token, create_refresh_token, decode_access_token, revoke_refresh_token


async def signup_user(
	user_repo: UserRepository,
	otp_repo: OtpRepository,
	payload: SignupRequest,
) -> tuple[User, str]:
	"""Create an unverified user (with hashed password) and return an email-verification OTP.

	If a user already exists for the email:
	- and is verified → raises 409 Conflict.
	- and is NOT verified → reuses the row, refreshes the password, and re-sends OTP.
	"""
	email = payload.email.strip().lower()
	existing = await user_repo.get_by_email(email)

	if existing is not None:
		if existing.is_email_verified:
			raise ConflictError("An account with this email already exists.")
		existing.password_hash = hash_password(payload.password)
		existing.first_name = payload.first_name.strip()
		existing.last_name = payload.last_name.strip()
		user = existing
	else:
		try:
			user = User(
				email=email,
				first_name=payload.first_name.strip(),
				last_name=payload.last_name.strip(),
				password_hash=hash_password(payload.password),
				role=UserRole.PATIENT,
				is_email_verified=False,
				is_active=True,
			)
			user_repo.add(user)
			await user_repo.flush()
		except IntegrityError:
			await user_repo.rollback()
			user = await user_repo.get_by_email(email)
			if user is None or user.is_email_verified:
				raise ConflictError("An account with this email already exists.")
			user.password_hash = hash_password(payload.password)
			user.first_name = payload.first_name.strip()
			user.last_name = payload.last_name.strip()

	_, code = await create_otp_for_user(otp_repo, user_id=user.id, purpose=OtpPurpose.EMAIL_VERIFICATION)
	await user_repo.commit()
	await user_repo.refresh(user)

	return user, code


async def authenticate_credentials(
	user_repo: UserRepository,
	*,
	email: str,
	password: str,
) -> tuple[User, str, int, str]:
	"""Verify email + password and return (user, access_token, ttl_seconds, refresh_token).

	Raises:
		NotFoundError: if the email is not registered.
		ForbiddenError: if the account is inactive or email is unverified.
		UnauthorizedError: if the password is wrong.
	"""
	user = await user_repo.get_by_email(email)
	if user is None:
		raise NotFoundError("No account found for this email.")
	if not user.is_active:
		raise ForbiddenError("This account is disabled.")
	if not user.is_email_verified:
		raise ForbiddenError("Email not verified. Check your inbox for the verification code we sent during signup.")
	if not user.password_hash or not verify_password(password, user.password_hash):
		raise UnauthorizedError("Incorrect email or password.")

	now = datetime.now(timezone.utc)
	user.last_login_at = now
	await user_repo.commit()
	await user_repo.refresh(user)

	token, ttl_seconds = create_access_token(user.id)
	refresh_token = await create_refresh_token(user.id)
	return user, token, ttl_seconds, refresh_token


async def authenticate_otp(
	user_repo: UserRepository,
	otp_repo: OtpRepository,
	*,
	email: str,
	code: str,
) -> tuple[User, str, int, str]:
	"""Verify an email-verification OTP and return (user, access_token, ttl_seconds, refresh_token).

	Flips `is_email_verified=True` on success.
	"""
	user = await user_repo.get_by_email(email)
	if user is None:
		raise UnauthorizedError("Invalid code.")
	if not user.is_active:
		raise ForbiddenError("This account is disabled.")

	try:
		await verify_otp_for_user(otp_repo, user_id=user.id, purpose=OtpPurpose.EMAIL_VERIFICATION, code=code)
	except OtpVerificationError as exc:
		await user_repo.commit()
		raise UnauthorizedError(str(exc)) from exc

	now = datetime.now(timezone.utc)
	user.is_email_verified = True
	user.last_login_at = now

	await user_repo.commit()
	await user_repo.refresh(user)

	token, ttl_seconds = create_access_token(user.id)
	refresh_token = await create_refresh_token(user.id)
	return user, token, ttl_seconds, refresh_token


async def resend_otp(
	user_repo: UserRepository,
	otp_repo: OtpRepository,
	*,
	email: str,
) -> tuple[User, str]:
	"""Re-issue an email-verification OTP and return the code."""
	user = await user_repo.get_by_email(email)
	if user is None:
		raise NotFoundError("No account found for this email.")
	if not user.is_active:
		raise ForbiddenError("This account is disabled.")
	if user.is_email_verified:
		raise ConflictError("Email is already verified. Use login instead.")

	_, code = await create_otp_for_user(otp_repo, user_id=user.id, purpose=OtpPurpose.EMAIL_VERIFICATION)
	await user_repo.commit()
	await user_repo.refresh(user)

	return user, code


async def start_email_change(
	user_repo: UserRepository,
	otp_repo: OtpRepository,
	*,
	user: User,
	new_email: str,
	password: str,
) -> tuple[User, str]:
	"""Start an authenticated email change and return the generated OTP code."""
	if not user.password_hash or not verify_password(password, user.password_hash):
		raise BadRequestError("Incorrect password")

	normalized_email = new_email.strip().lower()
	existing = await user_repo.get_by_email(normalized_email)
	if existing is not None and existing.id != user.id:
		raise ConflictError("Email already in use")

	_, code = await create_otp_for_user(otp_repo, user_id=user.id, purpose=OtpPurpose.EMAIL_VERIFICATION)
	user.pending_email = normalized_email
	user.email_change_token = code

	await user_repo.commit()
	await user_repo.refresh(user)
	return user, code


async def verify_email_change(
	user_repo: UserRepository,
	otp_repo: OtpRepository,
	*,
	user: User,
	token: str,
) -> User:
	"""Verify pending email change token and promote pending email to primary email."""
	if not user.pending_email or not user.email_change_token or user.email_change_token != token:
		raise BadRequestError("Invalid or expired token")

	try:
		await verify_otp_for_user(
			otp_repo,
			user_id=user.id,
			purpose=OtpPurpose.EMAIL_VERIFICATION,
			code=token,
		)
	except OtpVerificationError as exc:
		await user_repo.commit()
		raise BadRequestError("Invalid or expired token") from exc

	user.email = user.pending_email
	user.pending_email = None
	user.email_change_token = None

	try:
		await user_repo.commit()
	except IntegrityError as exc:
		await user_repo.rollback()
		raise ConflictError("Email already in use") from exc

	await user_repo.refresh(user)
	return user


def otp_ttl_seconds() -> int:
	return get_settings().OTP_EXPIRES_MINUTES * 60


async def update_profile(
	user_repo: UserRepository,
	*,
	user: User,
	first_name: str | None,
	last_name: str | None,
) -> User:
	"""Update the user's first and/or last name. Ignores None fields."""
	if first_name is not None:
		normalized = first_name.strip()
		if not normalized:
			raise BadRequestError("First name cannot be blank.")
		user.first_name = normalized
	if last_name is not None:
		normalized = last_name.strip()
		if not normalized:
			raise BadRequestError("Last name cannot be blank.")
		user.last_name = normalized
	return user


async def update_password(
	user_repo: UserRepository,
	blocklist_repo: TokenBlocklistRepository,
	*,
	user: User,
	current_password: str,
	new_password: str,
	access_token: str,
	refresh_token: str | None,
) -> None:
	"""Verify the current password then replace it with a new bcrypt hash.

	Raises BadRequestError if the current password is wrong.
	Revokes the refresh token first, then the access token, so all active
	sessions are immediately invalidated after the password change.
	"""
	if not user.password_hash or not verify_password(current_password, user.password_hash):
		raise BadRequestError("Incorrect password")
	user.password_hash = hash_password(new_password)
	if refresh_token:
		await revoke_refresh_token(refresh_token, blocklist_repo)
	payload = decode_access_token(access_token)
	jti: str = payload["jti"]
	expires_at = datetime.fromtimestamp(payload["exp"], tz=timezone.utc)
	await blocklist_repo.revoke(jti=jti, user_id=user.id, expires_at=expires_at)


async def delete_account(
	user_repo: UserRepository,
	blocklist_repo: TokenBlocklistRepository,
	*,
	user: User,
	access_token: str,
	refresh_token: str | None,
) -> None:
	"""Revoke active tokens then permanently delete the user.

	Refresh token is revoked first (if present), then the access token, then
	the user row is removed. The endpoint owns the commit so all three writes
	land atomically.
	"""
	if refresh_token:
		await revoke_refresh_token(refresh_token, blocklist_repo)
	payload = decode_access_token(access_token)
	jti: str = payload["jti"]
	expires_at = datetime.fromtimestamp(payload["exp"], tz=timezone.utc)
	await blocklist_repo.revoke(jti=jti, user_id=user.id, expires_at=expires_at)
	await user_repo.delete(user)
