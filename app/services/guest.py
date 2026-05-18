"""Guest session helpers backed by guest_sessions (Postgres)."""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from uuid import UUID

from app.core.exceptions import UnauthorizedError
from app.db.session import AsyncSessionLocal
from app.repositories.chat import ChatRepository
from app.repositories.guest_session import GuestSessionRepository
from app.repositories.medical_case import MedicalCaseRepository
from app.services.guest_sessions import GuestMigrationResult, GuestSessionManager

__all__ = [
	"GuestMigrationResult",
	"GuestSessionInfo",
	"create_guest_session",
	"get_guest_session",
	"migrate_guest_session_to_user",
	"normalize_guest_session_id",
	"resolve_guest_session_id",
	"revoke_guest_session",
	"to_guest_session_uuid",
	"touch_guest_session",
	"validate_guest_session",
]


@dataclass(frozen=True)
class GuestSessionInfo:
	"""Issued guest session metadata returned to clients."""

	guest_session_id: str
	expires_in: int


def _expires_in(session) -> int:
	now = datetime.now(timezone.utc)
	return max(0, int((session.expires_at - now).total_seconds()))


def session_info(session) -> GuestSessionInfo:
	return GuestSessionInfo(guest_session_id=str(session.id), expires_in=_expires_in(session))


def _manager(db) -> GuestSessionManager:
	return GuestSessionManager(GuestSessionRepository(db))


def to_guest_session_uuid(session_id: str | UUID) -> UUID:
	"""Parse a header/body token into a UUID (raises ValueError if invalid)."""
	if isinstance(session_id, UUID):
		return session_id
	normalized = normalize_guest_session_id(str(session_id))
	if normalized is None:
		raise ValueError("Invalid guest session id.")
	return UUID(normalized)


async def resolve_guest_session_id(
	session_id: str | UUID | None,
	*,
	manager: GuestSessionManager | None = None,
) -> UUID:
	"""Validate guest session in Postgres and return the canonical session UUID."""
	if session_id is None or (isinstance(session_id, str) and not session_id.strip()):
		raise UnauthorizedError("Missing guest session. Provide X-Guest-Session-Id or guest_session_id.")
	try:
		session_uuid = to_guest_session_uuid(session_id)
	except ValueError as exc:
		raise UnauthorizedError("Invalid guest session id.") from exc

	if manager is not None:
		session = await manager.get(session_uuid)
	else:
		async with AsyncSessionLocal() as db:
			session = await _manager(db).get(session_uuid)

	if session is None:
		raise UnauthorizedError("Guest session expired or invalid.")
	return session_uuid


def normalize_guest_session_id(session_id: str | UUID | None) -> str | None:
	"""Return canonical UUID string or None if the value is not a valid UUID."""
	if session_id is None:
		return None
	if isinstance(session_id, UUID):
		return str(session_id)
	if not session_id or not session_id.strip():
		return None
	try:
		return str(uuid.UUID(session_id.strip()))
	except ValueError:
		return None


async def create_guest_session(
	*,
	ip_hash: str = "test-ip-hash",
	device_fingerprint: str | None = None,
) -> GuestSessionInfo:
	"""Issue or reuse a guest session (test helper)."""
	fp = device_fingerprint if device_fingerprint is not None else f"test-{uuid.uuid4()}"
	async with AsyncSessionLocal() as db:
		session = await _manager(db).create(ip_hash, fp)
	return session_info(session)


async def get_guest_session(session_id: str) -> GuestSessionInfo | None:
	"""Return session metadata when the id is valid and still active."""
	normalized = normalize_guest_session_id(session_id)
	if normalized is None:
		return None
	async with AsyncSessionLocal() as db:
		session = await _manager(db).get(UUID(normalized))
	if session is None:
		return None
	return session_info(session)


async def validate_guest_session(session_id: str) -> bool:
	"""Return True when the session id exists and is active in Postgres."""
	normalized = normalize_guest_session_id(session_id)
	if normalized is None:
		return False
	async with AsyncSessionLocal() as db:
		session = await _manager(db).get(UUID(normalized))
	return session is not None


async def touch_guest_session(
	session_id: str | UUID,
	*,
	manager: GuestSessionManager | None = None,
) -> bool:
	"""Refresh session expiry on activity. Returns False if the session is missing or invalid."""
	normalized = normalize_guest_session_id(session_id)
	if normalized is None:
		return False

	if manager is not None:
		session = await manager.touch(UUID(normalized))
	else:
		async with AsyncSessionLocal() as db:
			session = await _manager(db).touch(UUID(normalized))

	return session is not None


async def revoke_guest_session(session_id: str) -> bool:
	"""Revoke a guest session in Postgres."""
	normalized = normalize_guest_session_id(session_id)
	if normalized is None:
		return False
	async with AsyncSessionLocal() as db:
		repo = GuestSessionRepository(db)
		row = await repo.get_by_id(UUID(normalized))
		if row is None:
			return False
		await _manager(db).revoke(UUID(normalized))
	return True


async def migrate_guest_session_to_user(
	case_repo: MedicalCaseRepository,
	chat_repo: ChatRepository,
	*,
	guest_session_id: str | UUID,
	user_id: UUID,
) -> GuestMigrationResult:
	"""Attach guest cases and orphan chats to the authenticated user; revoke guest session."""
	try:
		session_uuid = to_guest_session_uuid(guest_session_id)
	except ValueError:
		return GuestMigrationResult(cases_migrated=0, chats_updated=0)

	manager = GuestSessionManager(GuestSessionRepository(case_repo._session))
	result = await manager.migrate(session_uuid, user_id, case_repo, chat_repo)
	await revoke_guest_session(str(session_uuid))
	return result
