from dataclasses import dataclass
from typing import Annotated
from uuid import UUID

import jwt
from fastapi import Depends, Header, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import UnauthorizedError
from app.core.guest_session import (
	DEVICE_FINGERPRINT_HEADER,
	get_client_ip,
	hash_client_ip,
	normalize_device_fingerprint,
)
from app.db.session import get_session
from app.models.guest_session import GuestSession
from app.models.user import User
from app.repositories.ai_interpretation import AIInterpretationRepository
from app.repositories.auth_session import AuthSessionRepository
from app.repositories.chat import ChatRepository
from app.repositories.contact import ContactRepository
from app.repositories.guest_session import GuestSessionRepository
from app.repositories.lab_result import LabResultRepository
from app.repositories.medical_case import MedicalCaseRepository
from app.repositories.medical_upload import MedicalUploadRepository
from app.repositories.notification import NotificationRepository
from app.repositories.otp import OtpRepository
from app.repositories.password_reset import PasswordResetRepository
from app.repositories.pipeline_audit_log import PipelineAuditLogRepository
from app.repositories.token_blocklist import TokenBlocklistRepository
from app.repositories.user import UserRepository
from app.repositories.waitlist import WaitlistRepository
from app.services.auth.tokens import decode_access_token
from app.services.auth_sessions import AuthSessionManager
from app.services.events import EventBus
from app.services.guest import to_guest_session_uuid
from app.services.guest_sessions import GuestSessionManager
from app.services.websocket import ConnectionRegistry

DBSession = Annotated[AsyncSession, Depends(get_session)]

bearer_scheme = HTTPBearer(auto_error=False)


# Repository dependencies
def get_user_repo(session: DBSession) -> UserRepository:
	return UserRepository(session)


def get_otp_repo(session: DBSession) -> OtpRepository:
	return OtpRepository(session)


def get_token_blocklist_repo(session: DBSession) -> TokenBlocklistRepository:
	return TokenBlocklistRepository(session)


def get_password_reset_repo(session: DBSession) -> PasswordResetRepository:
	return PasswordResetRepository(session)


def get_medical_case_repo(session: DBSession) -> MedicalCaseRepository:
	return MedicalCaseRepository(session)


def get_lab_result_repo(session: DBSession) -> LabResultRepository:
	return LabResultRepository(session)


def get_ai_interpretation_repo(session: DBSession) -> AIInterpretationRepository:
	return AIInterpretationRepository(session)


def get_chat_repo(session: DBSession) -> ChatRepository:
	return ChatRepository(session)


def get_notification_repo(session: DBSession) -> NotificationRepository:
	return NotificationRepository(session)


def get_waitlist_repo(session: DBSession) -> WaitlistRepository:
	return WaitlistRepository(session)


def get_contact_repo(session: DBSession) -> ContactRepository:
	return ContactRepository(session)


def get_medical_upload_repo(session: DBSession) -> MedicalUploadRepository:
	return MedicalUploadRepository(session)


def get_guest_session_repo(session: DBSession) -> GuestSessionRepository:
	return GuestSessionRepository(session)


def get_auth_session_repo(session: DBSession) -> AuthSessionRepository:
	return AuthSessionRepository(session)


def get_auth_session_manager(
	auth_session_repo: Annotated[AuthSessionRepository, Depends(get_auth_session_repo)],
) -> AuthSessionManager:
	return AuthSessionManager(auth_session_repo)


def get_guest_session_manager(
	guest_session_repo: Annotated[GuestSessionRepository, Depends(get_guest_session_repo)],
) -> GuestSessionManager:
	return GuestSessionManager(guest_session_repo)


def get_pipeline_audit_log_repo(session: DBSession) -> PipelineAuditLogRepository:
	return PipelineAuditLogRepository(session)


# Annotated shortcuts
UserRepo = Annotated[UserRepository, Depends(get_user_repo)]
OtpRepo = Annotated[OtpRepository, Depends(get_otp_repo)]
TokenBlocklistRepo = Annotated[TokenBlocklistRepository, Depends(get_token_blocklist_repo)]
PasswordResetRepo = Annotated[PasswordResetRepository, Depends(get_password_reset_repo)]
MedicalCaseRepo = Annotated[MedicalCaseRepository, Depends(get_medical_case_repo)]
LabResultRepo = Annotated[LabResultRepository, Depends(get_lab_result_repo)]
AIInterpretationRepo = Annotated[AIInterpretationRepository, Depends(get_ai_interpretation_repo)]
ChatRepo = Annotated[ChatRepository, Depends(get_chat_repo)]
NotificationRepo = Annotated[NotificationRepository, Depends(get_notification_repo)]
WaitlistRepo = Annotated[WaitlistRepository, Depends(get_waitlist_repo)]
ContactRepo = Annotated[ContactRepository, Depends(get_contact_repo)]
GuestSessionRepo = Annotated[GuestSessionRepository, Depends(get_guest_session_repo)]
AuthSessionRepo = Annotated[AuthSessionRepository, Depends(get_auth_session_repo)]
AuthSessionManagerDep = Annotated[AuthSessionManager, Depends(get_auth_session_manager)]
GuestSessionManagerDep = Annotated[GuestSessionManager, Depends(get_guest_session_manager)]
PipelineAuditLogRepo = Annotated[PipelineAuditLogRepository, Depends(get_pipeline_audit_log_repo)]
MedicalUploadRepo = Annotated[MedicalUploadRepository, Depends(get_medical_upload_repo)]


# Auth guard


async def get_current_user(
	user_repo: UserRepo,
	blocklist_repo: TokenBlocklistRepo,
	credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(bearer_scheme)],
) -> User:
	"""Resolve the authenticated user from a Bearer JWT."""
	if credentials is None or credentials.scheme.lower() != "bearer":
		raise UnauthorizedError("Missing or invalid Authorization header.")

	try:
		payload = decode_access_token(credentials.credentials)
	except jwt.ExpiredSignatureError as exc:
		raise UnauthorizedError("Token has expired.") from exc
	except jwt.PyJWTError as exc:
		raise UnauthorizedError("Invalid authentication token.") from exc

	jti = payload.get("jti")
	if not jti or await blocklist_repo.is_revoked(jti):
		raise UnauthorizedError("Token has been revoked.")

	subject = payload.get("sub")
	if not subject:
		raise UnauthorizedError("Invalid authentication token.")

	try:
		user_id = UUID(str(subject))
	except ValueError as exc:
		raise UnauthorizedError("Invalid authentication token.") from exc

	user = await user_repo.get_by_id(user_id)
	if user is None or not user.is_active:
		raise UnauthorizedError("User not found or disabled.")
	if not user.is_email_verified:
		raise UnauthorizedError("Email address not verified.")
	return user


CurrentUser = Annotated[User, Depends(get_current_user)]


async def get_optional_user(
	user_repo: UserRepo,
	blocklist_repo: TokenBlocklistRepo,
	credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(bearer_scheme)],
) -> User | None:
	"""Resolve the authenticated user if a valid Bearer token is present, otherwise None."""
	if credentials is None or credentials.scheme.lower() != "bearer":
		return None
	try:
		payload = decode_access_token(credentials.credentials)
	except jwt.PyJWTError:
		return None

	jti = payload.get("jti")
	if not jti or await blocklist_repo.is_revoked(jti):
		return None

	subject = payload.get("sub")
	if not subject:
		return None

	try:
		user_id = UUID(str(subject))
	except ValueError:
		return None

	user = await user_repo.get_by_id(user_id)
	if user is None or not user.is_active or not user.is_email_verified:
		return None
	return user


def get_guest_session_id(x_guest_session_id: str | None = Header(None)) -> str | None:
	return x_guest_session_id


def get_device_fingerprint(
	x_device_fingerprint: str | None = Header(None, alias=DEVICE_FINGERPRINT_HEADER),
) -> str | None:
	return normalize_device_fingerprint(x_device_fingerprint)


def get_ip_hash(request: Request) -> str:
	return hash_client_ip(get_client_ip(request))


@dataclass
class SessionContext:
	user: User | None
	guest_session_id: str | None


OptionalUser = Annotated[User | None, Depends(get_optional_user)]
GuestSessionId = Annotated[str | None, Depends(get_guest_session_id)]
DeviceFingerprint = Annotated[str | None, Depends(get_device_fingerprint)]
ClientIpHash = Annotated[str, Depends(get_ip_hash)]


def get_session_context(
	user: OptionalUser,
	guest_session_id: GuestSessionId,
) -> SessionContext:
	return SessionContext(user=user, guest_session_id=guest_session_id)


async def get_current_guest_session(
	ctx: Annotated[SessionContext, Depends(get_session_context)],
	manager: GuestSessionManagerDep,
) -> GuestSession:
	"""Require a valid guest session (no authenticated user)."""
	if ctx.guest_session_id is None:
		raise UnauthorizedError("Missing or invalid guest session.")
	try:
		session_uuid = to_guest_session_uuid(ctx.guest_session_id)
	except ValueError as exc:
		raise UnauthorizedError("Invalid guest session id.") from exc
	session = await manager.get(session_uuid)
	if session is None:
		raise UnauthorizedError("Guest session expired or invalid.")
	return session


def get_event_bus(request: Request) -> EventBus:
	return request.app.state.event_bus


def get_connection_registry(request: Request) -> ConnectionRegistry:
	return request.app.state.connection_registry


SessionContextDep = Annotated[SessionContext, Depends(get_session_context)]
CurrentGuestSessionDep = Annotated[GuestSession, Depends(get_current_guest_session)]
EventBusDep = Annotated[EventBus, Depends(get_event_bus)]
ConnectionRegistryDep = Annotated[ConnectionRegistry, Depends(get_connection_registry)]
