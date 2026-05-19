"""Signed OAuth state token tests."""

from __future__ import annotations

import uuid

import pytest

from app.services.oauth_state import create_oauth_state, decode_oauth_state

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
