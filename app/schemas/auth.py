from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict, EmailStr, Field, field_validator, model_validator

from app.models.auth_session import AuthSession
from app.schemas.user import UserResponse


def _validate_person_name(value: str, *, label: str) -> str:
	if not value:
		raise ValueError(f"{label} cannot be empty.")
	if len(value) > 100:
		raise ValueError(f"{label} must be 100 characters or fewer.")
	return value


class SignupRequest(BaseModel):
	"""Signup form: first name, last name, email, password + confirm.

	After account creation a 6-digit OTP is emailed for address verification.
	"""

	model_config = ConfigDict(str_strip_whitespace=True)

	first_name: str
	last_name: str

	@field_validator("first_name")
	@classmethod
	def validate_first_name(cls, v: str) -> str:
		return _validate_person_name(v, label="First name")

	@field_validator("last_name")
	@classmethod
	def validate_last_name(cls, v: str) -> str:
		return _validate_person_name(v, label="Last name")

	email: EmailStr
	password: str = Field(min_length=8, max_length=72)
	confirm_password: str = Field(min_length=8, max_length=72)

	@field_validator("password")
	@classmethod
	def password_strength(cls, v: str) -> str:
		errors = []
		if len(v) < 8:
			errors.append("at least 8 characters")
		if not any(c.isupper() for c in v):
			errors.append("one uppercase letter")
		if not any(c.islower() for c in v):
			errors.append("one lowercase letter")
		if not any(c.isdigit() for c in v):
			errors.append("one number")
		if not any(c in "!@#$%^&*()_+-=[]{}|;':\",./<>?" for c in v):
			errors.append("one special character")
		if errors:
			raise ValueError("Password must contain " + ", ".join(errors) + ".")
		return v

	@model_validator(mode="after")
	def passwords_match(self) -> "SignupRequest":
		if self.password != self.confirm_password:
			raise ValueError("Passwords do not match.")
		return self


class LoginRequest(BaseModel):
	"""Login with email + password. Returns a JWT on success."""

	model_config = ConfigDict(str_strip_whitespace=True)

	email: EmailStr
	password: str = Field(min_length=1, max_length=72)
	device_id: str = Field(default="unknown", min_length=1, max_length=255)
	platform: str | None = Field(default="web", max_length=32)


class VerifyOtpRequest(BaseModel):
	"""Verify the email-verification OTP issued after signup."""

	model_config = ConfigDict(str_strip_whitespace=True)

	email: EmailStr
	code: str = Field(min_length=4, max_length=12)
	guest_session_id: str | None = None
	device_id: str = Field(default="unknown", min_length=1, max_length=255)
	platform: str | None = Field(default="web", max_length=32)


class ResendOtpRequest(BaseModel):
	model_config = ConfigDict(str_strip_whitespace=True)

	email: EmailStr


class TokenResponse(BaseModel):
	"""Returned after login, OTP verification, or token refresh."""

	access_token: str
	refresh_token: str
	token_type: str = "bearer"
	expires_in: int
	user: UserResponse | None = None

	model_config = ConfigDict(from_attributes=True)


class RefreshRequest(BaseModel):
	"""Optional body for token refresh (mobile clients without cookies)."""

	refresh_token: str | None = Field(default=None, min_length=1)


class TokenData(BaseModel):
	user_id: str | None = None


class TokenPair(BaseModel):
	status: str = "success"
	access_token: str
	refresh_token: str
	token_type: str = "bearer"


class OtpDispatchResponse(BaseModel):
	"""Returned after an OTP is dispatched (signup or resend)."""

	email: EmailStr
	expires_in_seconds: int


class ForgotPasswordRequest(BaseModel):
	email: EmailStr


class VerifyResetOtpRequest(BaseModel):
	model_config = ConfigDict(str_strip_whitespace=True)

	email: EmailStr
	code: str = Field(min_length=4, max_length=12)


class ResetTokenResponse(BaseModel):
	reset_token: str
	expires_in_seconds: int


class ResetPasswordRequest(BaseModel):
	email: EmailStr
	token: str = Field(min_length=6, max_length=512)
	new_password: str = Field(min_length=8, max_length=72)


class AuthSessionResponse(BaseModel):
	"""A per-device authenticated session (refresh token not exposed)."""

	id: UUID
	device_id: str
	platform: str | None = None
	created_at: datetime
	last_used_at: datetime | None = None
	revoked: bool = False

	model_config = ConfigDict(from_attributes=True)

	@classmethod
	def from_session(cls, row: AuthSession) -> "AuthSessionResponse":
		platform: str | None = None
		device_id = row.device_id
		if ":" in device_id:
			platform, _, device_id = device_id.partition(":")
		return cls(
			id=row.id,
			device_id=device_id,
			platform=platform,
			created_at=row.created_at,
			last_used_at=row.last_used_at,
			revoked=row.revoked,
		)


class GoogleAuthData(BaseModel):
	"""Response data for Google OAuth authentication."""

	access_token: str
	refresh_token: str
	token_type: str
	user: UserResponse
