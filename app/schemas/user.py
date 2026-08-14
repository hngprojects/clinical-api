from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict, EmailStr, Field

from app.models.user import UserRole


class UserBase(BaseModel):
	email: EmailStr
	first_name: str = Field(min_length=1, max_length=100)
	last_name: str = Field(min_length=1, max_length=100)
	role: UserRole = UserRole.PATIENT
	is_email_verified: bool = False
	is_active: bool = True


class UserCreate(UserBase):
	"""Schema for creating a user via email and password."""

	password: str


class GoogleUserCreate(UserBase):
	"""Schema for creating a user via Google OAuth."""

	google_id: str


class UserUpdate(BaseModel):
	"""Schema for updating a user profile."""

	first_name: str | None = Field(default=None, min_length=1, max_length=100)
	last_name: str | None = Field(default=None, min_length=1, max_length=100)
	is_active: bool | None = None


class ProfileUpdateRequest(BaseModel):
	"""Schema for updating first/last name."""

	first_name: str | None = Field(default=None, min_length=1, max_length=100)
	last_name: str | None = Field(default=None, min_length=1, max_length=100)


class PasswordUpdateRequest(BaseModel):
	"""Schema for changing the authenticated user's password."""

	current_password: str = Field(min_length=1, max_length=72)
	new_password: str = Field(min_length=8, max_length=72)


class EmailUpdateRequest(BaseModel):
	"""Schema for requesting an authenticated email change."""

	email: EmailStr
	password: str = Field(min_length=1, max_length=72)


class EmailUpdateVerifyRequest(BaseModel):
	"""Schema for verifying an authenticated email change."""

	token: str = Field(min_length=4, max_length=12)


class UserResponse(UserBase):
	"""Response schema for a user."""

	id: UUID
	google_id: str | None = None
	avatar_url: str | None = None
	created_at: datetime
	last_login_at: datetime | None = None

	model_config = ConfigDict(from_attributes=True)


class DashboardSummary(BaseModel):
	"""Nested dashboard payload easily extensible for future widgets/modules."""

	total_cases: int = 0
	pending_reviews: int = 0
	accepted_cases: int = 0
	completed_cases: int = 0
	earnings: float = 0.0

	model_config = ConfigDict(from_attributes=True)


class DoctorDutyStatusRequest(BaseModel):
	"""Request schema for updating doctor duty status."""

	is_on_duty: bool


class DoctorDutyStatusResponse(BaseModel):
	"""Response schema for doctor duty status."""

	is_on_duty: bool
	on_duty_since: datetime | None = None
	on_duty_expires_at: datetime | None = None
	remaining_duty_seconds: int = 0


class DoctorDashboardStatisticsResponse(BaseModel):
	"""Response schema for standalone doctor dashboard statistics."""

	pending_reviews: int = 0
	accepted_cases: int = 0
	completed_cases: int = 0
	earnings: float = 0.0
	total_cases: int = 0


class UserMeResponse(BaseModel):
	"""Primary endpoint response shape for profile data, verification status, and dashboard FE routing guard."""

	id: UUID
	email: str
	first_name: str
	last_name: str
	role: UserRole
	is_email_verified: bool
	is_active: bool
	avatar_url: str | None = None
	specialty: str | None = None
	verification_status: str = "not_submitted"
	rejection_reason: str | None = None
	is_on_duty: bool = False
	on_duty_expires_at: datetime | None = None
	remaining_duty_seconds: int = 0
	is_verification_dismissed: bool = False
	show_verification_banner: bool = True
	dashboard: DashboardSummary = Field(default_factory=DashboardSummary)
	created_at: datetime
	last_login_at: datetime | None = None

	model_config = ConfigDict(from_attributes=True)
