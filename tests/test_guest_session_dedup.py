"""PR2: guest session deduplication by IP hash + device fingerprint."""

from __future__ import annotations

import uuid

import pytest
from httpx import AsyncClient

from app.core.guest_session import DEVICE_FINGERPRINT_HEADER

pytestmark = pytest.mark.asyncio(loop_scope="session")

API = "/api/v1/guest-session"
LEGACY_API = "/api/v1/guest/sessions"


async def test_same_ip_and_fingerprint_returns_same_session(client: AsyncClient) -> None:
	headers = {DEVICE_FINGERPRINT_HEADER: "device-abc"}

	first = await client.post(API, headers=headers)
	second = await client.post(API, headers=headers)

	assert first.status_code == 201
	assert second.status_code == 201
	assert first.json()["data"]["guest_session_id"] == second.json()["data"]["guest_session_id"]


async def test_different_fingerprint_returns_new_session(client: AsyncClient) -> None:
	first = await client.post(API, headers={DEVICE_FINGERPRINT_HEADER: "device-a"})
	second = await client.post(API, headers={DEVICE_FINGERPRINT_HEADER: "device-b"})

	assert first.json()["data"]["guest_session_id"] != second.json()["data"]["guest_session_id"]


async def test_legacy_guest_sessions_path_dedupes(client: AsyncClient) -> None:
	fp = f"legacy-{uuid.uuid4().hex[:8]}"
	headers = {DEVICE_FINGERPRINT_HEADER: fp}

	first = await client.post(LEGACY_API, headers=headers)
	second = await client.post(LEGACY_API, headers=headers)

	assert first.status_code == 201
	assert second.status_code == 201
	assert first.json()["data"]["guest_session_id"] == second.json()["data"]["guest_session_id"]
