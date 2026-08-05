import os
from functools import lru_cache

from pydantic import Field, PostgresDsn, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

APP_ENV = os.getenv("APP_ENV", "staging")

ENV_FILES = {
	"dev": ".env",
	"staging": ".env.staging",
	"production": ".env.production",
}


class Settings(BaseSettings):
	model_config = SettingsConfigDict(
		env_file=ENV_FILES.get(APP_ENV, ".env"),
		env_file_encoding="utf-8",
		case_sensitive=True,
		extra="ignore",
	)

	PROJECT_NAME: str = "Clinsights"
	API_V1_PREFIX: str = "/api/v1"

	DATABASE_URL: PostgresDsn

	# CORS
	CORS_ORIGINS: list[str] = Field(default_factory=list)

	# Google OAuth
	GOOGLE_CLIENT_ID: str = ""
	GOOGLE_CLIENT_SECRET: str = ""
	GOOGLE_REDIRECT_URI: str = ""

	# JWT
	JWT_SECRET: str = Field(min_length=32)
	JWT_ALGORITHM: str = "HS256"
	JWT_ACCESS_TOKEN_EXPIRES_MINUTES: int = 60
	JWT_REFRESH_TOKEN_EXPIRES_MINUTES: int = 60 * 24 * 3  # 3 days

	# OTP
	OTP_LENGTH: int = 6
	OTP_EXPIRES_MINUTES: int = 10
	OTP_MAX_ATTEMPTS: int = 5
	OTP_PEPPER: str = Field(min_length=32)
	OTP_FAILURE_RATE_LIMIT: int = Field(default=5, ge=1)
	OTP_FAILURE_RATE_WINDOW_SECONDS: int = Field(default=300, ge=1)
	FORGOT_PASSWORD_RATE_LIMIT: int = Field(default=3, ge=1)
	FORGOT_PASSWORD_RATE_WINDOW_SECONDS: int = Field(default=600, ge=1)
	RESEND_OTP_RATE_LIMIT: int = Field(default=3, ge=1)
	RESEND_OTP_RATE_WINDOW_SECONDS: int = Field(default=300, ge=1)
	RESET_PASSWORD_RATE_LIMIT: int = Field(default=5, ge=1)
	RESET_PASSWORD_RATE_WINDOW_SECONDS: int = Field(default=600, ge=1)
	EMAIL_UPDATE_REQUEST_RATE_LIMIT: int = Field(default=3, ge=1)
	EMAIL_UPDATE_REQUEST_RATE_WINDOW_SECONDS: int = Field(default=600, ge=1)
	EMAIL_UPDATE_VERIFY_RATE_LIMIT: int = Field(default=5, ge=1)
	EMAIL_UPDATE_VERIFY_RATE_WINDOW_SECONDS: int = Field(default=300, ge=1)

	BREVO_API_KEY: str | None = Field(default=None)
	BREVO_FROM_EMAIL: str = Field(default="")
	BREVO_FROM_NAME: str = "Clinsights"

	# Test settings (Play Store / App Store reviewer bypass)
	STATIC_TEST_OTP_CODE: str = "123456"
	TEST_REVIEWER_EMAILS: list[str] = Field(
		default_factory=lambda: ["playstore.reviewer@clinsights.com"]
	)

	@field_validator("TEST_REVIEWER_EMAILS", mode="before")
	@classmethod
	def parse_reviewer_emails(cls, v: object) -> list[str]:
		if isinstance(v, str):
			return [e.strip().lower() for e in v.split(",") if e.strip()]
		if isinstance(v, list):
			return [str(e).strip().lower() for e in v if str(e).strip()]
		return ["playstore.reviewer@clinsights.com"]


	# SMTP (fallback email provider)
	SMTP_HOST: str = ""
	SMTP_PORT: str = ""
	SMTP_USERNAME: str | None = None
	SMTP_PASSWORD: str | None = None
	SMTP_FROM_EMAIL: str = ""
	SMTP_FROM_NAME: str = "Clinsights"
	SMTP_USE_TLS: bool = True

	COOKIE_SECURE: bool = False
	COOKIE_SAMESITE: str = "strict"
	ALLOW_STDOUT_EMAIL: bool = False

	CELERY_BROKER_URL: str = "redis://localhost:6379/0"
	CELERY_RESULT_BACKEND: str | None = None

	# Guest sessions (Postgres guest_sessions table)
	GUEST_SESSION_TTL_SECONDS: int = 3600  # 1 hour
	GUEST_CHAT_MESSAGE_LIMIT: int = 3
	GUEST_UPLOAD_LIMIT: int = 1
	GUEST_UPLOAD_LOCK_SECONDS: int = 1200
	GUEST_SESSION_RETENTION_DAYS: int = 7
	GUEST_SESSION_CREATE_RATE_LIMIT: int = 30
	GUEST_SESSION_CREATE_RATE_WINDOW_SECONDS: int = 3600

	SIGNUP_RATE_LIMIT: int = 5
	SIGNUP_RATE_WINDOW_SECONDS: int = 3600
	WAITLIST_RATE_LIMIT: int = Field(default=5, ge=1)
	WAITLIST_RATE_WINDOW_SECONDS: int = Field(default=3600, ge=1)
	LOGIN_FAILURE_RATE_LIMIT: int = Field(default=10, ge=1)
	LOGIN_FAILURE_RATE_WINDOW_SECONDS: int = Field(default=900, ge=1)
	# When true, use X-Forwarded-For (first hop) for guest IP hashing; only enable behind a trusted proxy.
	TRUST_PROXY_FORWARDED_IP: bool = False

	# Auth sessions (Postgres auth_sessions table)
	AUTH_SESSION_INACTIVITY_DAYS: int = 30
	AUTH_SESSION_ABSOLUTE_DAYS: int = 90

	# OAuth
	OAUTH_STATE_EXPIRES_MINUTES: int = 60

	AI_PROVIDER: str = "auto"

	OPENAI_API_KEY: str = ""
	OPENAI_MODEL: str = "gpt-4o-mini"

	GEMINI_API_KEY: str = ""
	GEMINI_MODEL: str = "gemini-3-flash-preview"

	PIPELINE_TIMEOUT_SECONDS: int = 30

	FRONTEND_URL: str = ""

	MEDIA_DIR: str = "media"
	PRIVATE_MEDIA_DIR: str = "private_media"
	R2_ACCOUNT_ID: str = ""
	R2_ACCESS_KEY_ID: str = ""
	R2_SECRET_ACCESS_KEY: str = ""
	R2_BUCKET_NAME: str = ""
	R2_CUSTOM_DOMAIN: str = ""
	RESEND_API_KEY: str | None = None
	RESEND_FROM_EMAIL: str = ""
	MAILERLITE_API_KEY: str | None = None

	@field_validator("RESEND_FROM_EMAIL", mode="after")
	@classmethod
	def resend_from_email_required_when_resend_enabled(cls, v: str, info: object) -> str:
		"""Require a non-empty RESEND_FROM_EMAIL when Resend is configured."""
		data = getattr(info, "data", {})
		if data.get("RESEND_API_KEY") and not v:
			raise ValueError("RESEND_FROM_EMAIL must be set when RESEND_API_KEY is configured")
		return v

	# Password reset
	FRONTEND_RESET_PASSWORD_URL: str = f"{FRONTEND_URL}/reset-password"
	FRONTEND_AUTH_CALLBACK_URL: str = f"{FRONTEND_URL}/auth/callback"
	PASSWORD_RESET_TOKEN_EXPIRES_MINUTES: int = 60


@lru_cache
def get_settings() -> Settings:
	return Settings()  # type: ignore[call-arg]
