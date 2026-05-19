import os
from functools import lru_cache

from pydantic import Field, PostgresDsn
from pydantic_settings import BaseSettings, SettingsConfigDict

APP_ENV = os.getenv("APP_ENV", "staging")

ENV_FILES = {
	"staging": ".env.staging",
	"production": ".env.production",
}


class Settings(BaseSettings):
	model_config = SettingsConfigDict(
		env_file=ENV_FILES.get(APP_ENV, ".env.staging"),
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
	JWT_ACCESS_TOKEN_EXPIRES_MINUTES: int = 3
	JWT_REFRESH_TOKEN_EXPIRES_MINUTES: int = 5

	# OTP
	OTP_LENGTH: int = 6
	OTP_EXPIRES_MINUTES: int = 10
	OTP_MAX_ATTEMPTS: int = 5
	OTP_PEPPER: str = Field(min_length=32)

	BREVO_API_KEY: str | None = Field(default=None)
	BREVO_FROM_EMAIL: str = Field(default="")
	BREVO_FROM_NAME: str = "Clinsights"

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
	GUEST_SESSION_RETENTION_DAYS: int = 7
	GUEST_SESSION_CREATE_RATE_LIMIT: int = 30
	GUEST_SESSION_CREATE_RATE_WINDOW_SECONDS: int = 3600
	# When true, use X-Forwarded-For (first hop) for guest IP hashing; only enable behind a trusted proxy.
	TRUST_PROXY_FORWARDED_IP: bool = False

	# Auth sessions (Postgres auth_sessions table)
	AUTH_SESSION_INACTIVITY_DAYS: int = 30
	AUTH_SESSION_ABSOLUTE_DAYS: int = 90

	# OAuth
	OAUTH_STATE_EXPIRES_MINUTES: int = 10

	AI_PROVIDER: str = "auto"

	OPENAI_API_KEY: str = ""
	OPENAI_MODEL: str = "gpt-4o-mini"

	GEMINI_API_KEY: str = ""
	GEMINI_MODEL: str = "gemini-2.0-flash"

	PIPELINE_TIMEOUT_SECONDS: int = 30

	FRONTEND_URL: str = ""

	# Password reset
	FRONTEND_RESET_PASSWORD_URL: str = f"{FRONTEND_URL}/reset-password"
	FRONTEND_AUTH_CALLBACK_URL: str = f"{FRONTEND_URL}/auth/callback"
	PASSWORD_RESET_TOKEN_EXPIRES_MINUTES: int = 60


@lru_cache
def get_settings() -> Settings:
	return Settings()  # type: ignore[call-arg]
