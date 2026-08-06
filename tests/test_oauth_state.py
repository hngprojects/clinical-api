"""Signed OAuth state token tests."""

from __future__ import annotations

import uuid
from types import SimpleNamespace
from urllib.parse import parse_qs, urlparse

import pytest

from app.services import oauth_state as oauth_state_service
from app.services.oauth_state import build_redirect_url, create_oauth_state, decode_oauth_state

pytestmark = pytest.mark.no_db


def test_oauth_state_round_trip_with_guest_and_device() -> None:
	guest_id = str(uuid.uuid4())
	token = create_oauth_state(
		guest_session_id=guest_id,
		device_id="pixel-8",
		platform="android",
	)
	parsed = decode_oauth_state(token)
	assert parsed is not None
	assert parsed.guest_session_id == guest_id
	assert parsed.device_id == "pixel-8"
	assert parsed.platform == "android"


def test_oauth_state_rejects_tampered_token() -> None:
	token = create_oauth_state(guest_session_id=str(uuid.uuid4()))
	assert decode_oauth_state(token + "x") is None


def test_oauth_state_empty_state() -> None:
	parsed = decode_oauth_state("")
	assert parsed is not None
	assert parsed.guest_session_id is None


def test_oauth_state_only_keeps_allowed_return_url(monkeypatch: pytest.MonkeyPatch) -> None:
	monkeypatch.setattr(
		oauth_state_service,
		"get_settings",
		lambda: SimpleNamespace(
			JWT_SECRET="x" * 32,
			JWT_ALGORITHM="HS256",
			OAUTH_STATE_EXPIRES_MINUTES=10,
			FRONTEND_AUTH_CALLBACK_URL="https://frontend.example/login",
		),
	)

	allowed_token = create_oauth_state(return_url="clinsight://auth/google")
	allowed_parsed = decode_oauth_state(allowed_token)
	assert allowed_parsed is not None
	assert allowed_parsed.return_url == "clinsight://auth/google"

	allowed_web_token = create_oauth_state(return_url="https://frontend.example/login")
	allowed_web_parsed = decode_oauth_state(allowed_web_token)
	assert allowed_web_parsed is not None
	assert allowed_web_parsed.return_url == "https://frontend.example/login"

	allowed_with_query_token = create_oauth_state(return_url="clinsight://auth/google?next=/dashboard#ignore")
	allowed_with_query_parsed = decode_oauth_state(allowed_with_query_token)
	assert allowed_with_query_parsed is not None
	assert allowed_with_query_parsed.return_url == "clinsight://auth/google"

	blocked_token = create_oauth_state(return_url="https://evil.example/login")
	blocked_parsed = decode_oauth_state(blocked_token)
	assert blocked_parsed is not None
	assert blocked_parsed.return_url is None


def test_build_redirect_url_merges_existing_query_params() -> None:
	redirect_url = build_redirect_url(
		"clinsight://auth/google?existing=1",
		"token-123",
		refresh_token="refresh-456",
	)
	parsed = urlparse(redirect_url)
	assert parsed.scheme == "clinsight"
	assert parsed.netloc == "auth"
	assert parsed.path == "/google"
	assert parse_qs(parsed.query) == {
		"existing": ["1"],
		"access_token": ["token-123"],
		"refresh_token": ["refresh-456"],
	}
