"""Per-device authenticated sessions backed by auth_sessions (Postgres)."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from uuid import UUID

import jwt

from app.core.exceptions import NotFoundError, UnauthorizedError
from app.core.security import hash_opaque_token
from app.models.auth_session import AuthSession
from app.repositories.auth_session import AuthSessionRepository
from app.services.auth.tokens import create_access_token, create_refresh_token, decode_refresh_token


@dataclass(frozen=True)
class AuthSessionIssue:
	"""Tokens issued for a new or rotated device session."""

	session: AuthSession
	access_token: str
	refresh_token: str
	expires_in: int


class AuthSessionManager:
	"""Manage refresh-token ownership per user device."""

	def __init__(self, repo: AuthSessionRepository) -> None:
		self._repo = repo

	@staticmethod
	def _now() -> datetime:
		return datetime.now(timezone.utc)

	@staticmethod
	def _refresh_hash(raw_refresh_token: str) -> str:
		return hash_opaque_token(raw_refresh_token)

	@staticmethod
	def _device_key(device_id: str, platform: str | None) -> str:
		normalized = device_id.strip()[:255]
		if platform and platform.strip():
			return f"{platform.strip().lower()}:{normalized}"
		return normalized

	async def create(
		self,
		user_id: UUID,
		device_id: str,
		*,
		platform: str | None = "web",
		ip_hash: str | None = None,
		user_agent: str | None = None,
	) -> AuthSessionIssue:
		"""Create a device session row and return access + refresh tokens."""
		now = self._now()
		device_key = self._device_key(device_id, platform)

		existing = await self._repo.get_active_by_user_and_device(user_id, device_key)
		if existing is not None:
			existing.revoked = True
			existing.revoked_at = now

		refresh_token = await create_refresh_token(user_id)
		access_token, expires_in = create_access_token(user_id)

		session = AuthSession(
			user_id=user_id,
			device_id=device_key,
			refresh_token=self._refresh_hash(refresh_token),
			ip_hash=ip_hash,
			user_agent=user_agent,
			created_at=now,
			last_used_at=now,
			revoked=False,
		)
		self._repo.add(session)
		await self._repo.commit()
		return AuthSessionIssue(
			session=session,
			access_token=access_token,
			refresh_token=refresh_token,
			expires_in=expires_in,
		)

	async def validate_refresh(self, raw_refresh_token: str) -> AuthSession:
		"""Decode refresh JWT and ensure a matching active auth_sessions row exists."""
		try:
			decode_refresh_token(raw_refresh_token)
		except jwt.PyJWTError as exc:
			raise UnauthorizedError("Invalid refresh token.") from exc

		row = await self._repo.get_by_refresh_token(self._refresh_hash(raw_refresh_token))
		if row is None or row.revoked:
			raise UnauthorizedError("Refresh token has been revoked.")
		return row

	async def rotate_refresh(self, raw_refresh_token: str) -> AuthSessionIssue:
		"""Validate the current refresh token and issue a rotated pair for the same device."""
		session = await self.validate_refresh(raw_refresh_token)
		now = self._now()

		new_refresh = await create_refresh_token(session.user_id)
		access_token, expires_in = create_access_token(session.user_id)

		session.refresh_token = self._refresh_hash(new_refresh)
		session.last_used_at = now
		session.revoked = False
		session.revoked_at = None
		await self._repo.commit()

		return AuthSessionIssue(
			session=session,
			access_token=access_token,
			refresh_token=new_refresh,
			expires_in=expires_in,
		)

	async def revoke_by_refresh_token(self, raw_refresh_token: str) -> AuthSession | None:
		"""Revoke the auth session matching a refresh token, if present."""
		try:
			decode_refresh_token(raw_refresh_token)
		except jwt.PyJWTError:
			return None

		session = await self._repo.get_by_refresh_token(self._refresh_hash(raw_refresh_token))
		if session is None:
			return None
		if not session.revoked:
			session.revoked = True
			session.revoked_at = self._now()
			await self._repo.commit()
		return session

	async def revoke(self, session_id: UUID, user_id: UUID) -> None:
		"""Revoke a single device session owned by the user."""
		session = await self._repo.get_by_id_for_user(session_id, user_id)
		if session is None:
			raise NotFoundError("Session not found.")
		if not session.revoked:
			session.revoked = True
			session.revoked_at = self._now()
			await self._repo.commit()

	async def revoke_all(self, user_id: UUID) -> int:
		"""Revoke every active session for the user. Returns count revoked."""
		now = self._now()
		return await self._repo.revoke_all_for_user(user_id, revoked_at=now)

	async def list_sessions(self, user_id: UUID) -> list[AuthSession]:
		"""List non-revoked sessions for the user (newest first)."""
		return await self._repo.list_active_by_user(user_id)
