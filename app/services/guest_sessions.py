"""Guest session lifecycle backed by guest_sessions (Postgres)."""

from __future__ import annotations

import enum
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from uuid import UUID

from app.core.config import get_settings
from app.core.exceptions import GuestLimitExceeded, UnauthorizedError
from app.models.guest_session import GuestSession
from app.repositories.chat import ChatRepository
from app.repositories.guest_session import GuestSessionRepository
from app.repositories.medical_case import MedicalCaseRepository


class GuestUsageAction(enum.StrEnum):
	CHAT = "chat"
	UPLOAD = "upload"


@dataclass(frozen=True)
class GuestMigrationResult:
	cases_migrated: int
	chats_updated: int


class GuestSessionManager:
	"""Create, resolve, and revoke server-owned guest sessions."""

	def __init__(self, repo: GuestSessionRepository) -> None:
		self._repo = repo

	@staticmethod
	def _now() -> datetime:
		return datetime.now(timezone.utc)

	@staticmethod
	def is_active(session: GuestSession, *, now: datetime | None = None) -> bool:
		current = now or GuestSessionManager._now()
		return not session.revoked and session.migrated_user_id is None and session.expires_at > current

	async def create(
		self,
		ip_hash: str,
		device_fingerprint: str | None,
	) -> GuestSession:
		"""Return an active session for this device, creating one if needed."""
		now = self._now()
		existing = await self._repo.get_active_by_device(
			ip_hash,
			device_fingerprint,
			now=now,
		)
		if existing is not None:
			return await self._touch(existing, now=now)

		settings = get_settings()
		session = GuestSession(
			id=uuid.uuid4(),
			ip_hash=ip_hash,
			device_fingerprint=device_fingerprint,
			chat_count=0,
			upload_count=0,
			expires_at=now + timedelta(seconds=settings.GUEST_SESSION_TTL_SECONDS),
			last_active_at=now,
			revoked=False,
		)
		self._repo.add(session)
		await self._repo.commit()
		return session

	async def get(self, guest_session_id: UUID) -> GuestSession | None:
		"""Load a guest session if it exists and is still active."""
		session = await self._repo.get_by_id(guest_session_id)
		if session is None or not self.is_active(session):
			return None
		return session

	async def can_use(self, guest_session_id: UUID, action: GuestUsageAction) -> GuestSession:
		"""Ensure the guest session may perform an action; raise GuestLimitExceeded if not."""
		session = await self.get(guest_session_id)
		if session is None:
			raise UnauthorizedError("Guest session expired or invalid.")

		settings = get_settings()
		if action == GuestUsageAction.CHAT and session.chat_count >= settings.GUEST_CHAT_MESSAGE_LIMIT:
			raise GuestLimitExceeded("Guest message limit reached. Please sign up to continue chatting.")
		if action == GuestUsageAction.UPLOAD and session.upload_count >= settings.GUEST_UPLOAD_LIMIT:
			raise GuestLimitExceeded("Upload limit reached. Please sign up to upload more results.")
		return session

	async def increment_chat(self, guest_session_id: UUID) -> None:
		session = await self._repo.get_by_id(guest_session_id)
		if session is None:
			raise UnauthorizedError("Guest session expired or invalid.")
		session.chat_count += 1
		await self._touch(session, now=self._now())

	async def increment_upload(self, guest_session_id: UUID) -> None:
		session = await self._repo.get_by_id(guest_session_id)
		if session is None:
			raise UnauthorizedError("Guest session expired or invalid.")
		session.upload_count += 1
		await self._touch(session, now=self._now())

	async def touch(self, guest_session_id: UUID) -> GuestSession | None:
		"""Extend TTL and update last_active_at for an active session."""
		session = await self.get(guest_session_id)
		if session is None:
			return None
		return await self._touch(session, now=self._now())

	async def revoke(self, guest_session_id: UUID) -> None:
		"""Mark a guest session revoked (e.g. after migration)."""
		session = await self._repo.get_by_id(guest_session_id)
		if session is None:
			return
		session.revoked = True
		await self._repo.commit()

	async def migrate(
		self,
		guest_session_id: UUID,
		user_id: UUID,
		case_repo: MedicalCaseRepository,
		chat_repo: ChatRepository,
	) -> GuestMigrationResult:
		"""Atomically attach guest cases/chats to a user and revoke the guest session."""
		if case_repo._session is not self._repo._session:
			raise RuntimeError("migrate() requires repositories on the same database session.")

		guest_row = await self._repo.get_by_id(guest_session_id)
		if guest_row is None or guest_row.revoked or guest_row.migrated_user_id is not None:
			return GuestMigrationResult(cases_migrated=0, chats_updated=0)

		cases = await case_repo.get_by_guest_session(guest_session_id, offset=0, limit=500)
		guest_row.migrated_user_id = user_id
		guest_row.revoked = True

		case_ids: list[UUID] = []
		for case in cases:
			case.user_id = user_id
			case.guest_session_id = None
			case_ids.append(case.id)

		chats_updated = await chat_repo.assign_user_to_case_messages(case_ids, user_id)
		await self._repo.commit()
		return GuestMigrationResult(cases_migrated=len(cases), chats_updated=chats_updated)

	async def _touch(self, session: GuestSession, *, now: datetime) -> GuestSession:
		settings = get_settings()
		session.last_active_at = now
		session.expires_at = now + timedelta(seconds=settings.GUEST_SESSION_TTL_SECONDS)
		await self._repo.commit()
		return session
