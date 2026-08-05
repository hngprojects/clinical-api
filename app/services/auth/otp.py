import hashlib
import hmac
import secrets
from datetime import datetime, timedelta, timezone
from uuid import UUID

from app.core.config import get_settings
from app.models.otp import OtpCode, OtpPurpose
from app.repositories.otp import OtpRepository


def _generate_numeric_code(length: int) -> str:
	"""Cryptographically random zero-padded numeric code."""
	upper = 10**length
	return f"{secrets.randbelow(upper):0{length}d}"


def _hash_code(code: str) -> str:
	"""Salt the code with the server-side pepper and hash with SHA-256."""
	pepper = get_settings().OTP_PEPPER.encode("utf-8")
	return hashlib.sha256(pepper + code.encode("utf-8")).hexdigest()


def _codes_match(code: str, code_hash: str) -> bool:
	return hmac.compare_digest(_hash_code(code), code_hash)


async def create_otp_for_user(
	otp_repo: OtpRepository,
	*,
	user_id: UUID,
	purpose: OtpPurpose,
) -> tuple[OtpCode, str]:
	"""Invalidate previous active OTPs of `purpose` for the user, then mint a new one.

	Returns the persisted `OtpCode` row and the **plaintext** code (the only
	moment it's available — caller is responsible for delivering it).
	"""
	now = datetime.now(timezone.utc)

	await otp_repo.invalidate_active(user_id=user_id, purpose=purpose, consumed_at=now)

	settings = get_settings()
	code = _generate_numeric_code(settings.OTP_LENGTH)
	otp = OtpCode(
		user_id=user_id,
		code_hash=_hash_code(code),
		purpose=purpose,
		expires_at=now + timedelta(minutes=settings.OTP_EXPIRES_MINUTES),
	)
	otp_repo.add(otp)
	await otp_repo.flush()
	return otp, code


class OtpVerificationError(Exception):
	"""Raised when an OTP cannot be verified."""


async def verify_otp_for_user(
	otp_repo: OtpRepository,
	*,
	user_id: UUID,
	purpose: OtpPurpose,
	code: str,
	user_email: str | None = None,
) -> OtpCode:
	"""Verify `code` against the latest active OTP for the user/purpose.

	On success the OTP is marked consumed and returned. On failure raises
	`OtpVerificationError` with a user-safe message.
	"""
	now = datetime.now(timezone.utc)
	settings = get_settings()

	# Reviewer static test OTP bypass
	if user_email and settings.STATIC_TEST_OTP_CODE:
		normalized_email = user_email.strip().lower()
		reviewer_emails = [e.strip().lower() for e in settings.TEST_REVIEWER_EMAILS]
		if normalized_email in reviewer_emails and code.strip() == settings.STATIC_TEST_OTP_CODE:
			otp = await otp_repo.get_latest_active(user_id=user_id, purpose=purpose, lock=True)
			if otp is not None:
				otp.consumed_at = now
				return otp
			mock_otp = OtpCode(
				user_id=user_id,
				code_hash=_hash_code(settings.STATIC_TEST_OTP_CODE),
				purpose=purpose,
				expires_at=now + timedelta(minutes=10),
				consumed_at=now,
			)
			return mock_otp

	otp = await otp_repo.get_latest_active(user_id=user_id, purpose=purpose, lock=True)

	if otp is None:
		raise OtpVerificationError("No active code found. Request a new one.")

	if otp.expires_at <= now:
		otp.consumed_at = now
		raise OtpVerificationError("This code has expired. Please request a new one.")

	if otp.attempts >= settings.OTP_MAX_ATTEMPTS:
		otp.consumed_at = now
		raise OtpVerificationError("Too many incorrect attempts. Request a new code.")

	if not _codes_match(code, otp.code_hash):
		otp.attempts += 1
		if otp.attempts >= settings.OTP_MAX_ATTEMPTS:
			otp.consumed_at = now
		raise OtpVerificationError("The code you entered is incorrect.")

	otp.consumed_at = now
	return otp
