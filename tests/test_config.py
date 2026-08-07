"""Settings and frontend URL derivation tests."""

from __future__ import annotations

import pytest

from app.core.config import Settings

pytestmark = pytest.mark.no_db

_SETTINGS_KWARGS = {
	"DATABASE_URL": "postgresql+asyncpg://postgres:postgres@localhost:5432/test",
	"JWT_SECRET": "x" * 32,
	"OTP_PEPPER": "x" * 32,
}


def test_derive_frontend_urls_from_frontend_url() -> None:
	settings = Settings(FRONTEND_URL="http://localhost:3000", **_SETTINGS_KWARGS)

	assert settings.FRONTEND_AUTH_CALLBACK_URL == "http://localhost:3000/login"
	assert settings.FRONTEND_RESET_PASSWORD_URL == "http://localhost:3000/reset-password"


def test_derive_frontend_urls_strips_trailing_slash() -> None:
	settings = Settings(FRONTEND_URL="http://localhost:3000/", **_SETTINGS_KWARGS)

	assert settings.FRONTEND_AUTH_CALLBACK_URL == "http://localhost:3000/login"
	assert settings.FRONTEND_RESET_PASSWORD_URL == "http://localhost:3000/reset-password"


def test_explicit_frontend_urls_are_not_overridden() -> None:
	settings = Settings(
		FRONTEND_URL="http://localhost:3000",
		FRONTEND_AUTH_CALLBACK_URL="https://custom.example/oauth/callback",
		FRONTEND_RESET_PASSWORD_URL="https://custom.example/reset-password",
		**_SETTINGS_KWARGS,
	)

	assert settings.FRONTEND_AUTH_CALLBACK_URL == "https://custom.example/oauth/callback"
	assert settings.FRONTEND_RESET_PASSWORD_URL == "https://custom.example/reset-password"


def test_empty_frontend_url_leaves_callback_urls_empty() -> None:
	settings = Settings(**_SETTINGS_KWARGS)

	assert settings.FRONTEND_URL == ""
	assert settings.FRONTEND_AUTH_CALLBACK_URL == ""
	assert settings.FRONTEND_RESET_PASSWORD_URL == ""
