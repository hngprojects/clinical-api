import uuid
from datetime import datetime, timedelta, timezone

from app.core.exceptions import NotFoundError
from app.models.guest_session import GuestSession
from app.repositories.guest_session import GuestSessionRepository
from app.repositories.medical_case import MedicalCaseRepository


async def create_guest_session(
	guest_repo: GuestSessionRepository,
) -> GuestSession:
	"""
	Generate a new guest session with a 1 hour TTL.
	Returns the session so the caller can give the session_id to the client.
	"""
	now = datetime.now(timezone.utc)
	session = GuestSession(
		session_id=str(uuid.uuid4()),
		expires_at=now + timedelta(hours=1),
		created_at=now,
		last_active_at=now,
	)
	guest_repo.add(session)
	await guest_repo.commit()
	await guest_repo.refresh(session)
	return session


async def validate_guest_session(
	guest_repo: GuestSessionRepository,
	session_id: str,
) -> GuestSession:
	"""
	Validate a guest session ID. Raises 404 if not found or expired.
	Updates last_active_at on success.
	"""
	session = await guest_repo.get_by_session_id(session_id)
	if session is None:
		raise NotFoundError("Guest session not found.")
	if session.expires_at <= datetime.now(timezone.utc):
		raise NotFoundError("Guest session has expired.")
	await guest_repo.touch(session_id)
	await guest_repo.commit()
	return session


async def migrate_guest_cases(
	guest_session_id: str,
	user_id: uuid.UUID,
	medical_case_repo: MedicalCaseRepository,
) -> int:
	"""
	Link all MedicalCase records from a guest session to a real user account.
	Called at the end of OTP verification when the user signs up.
	Returns the number of cases migrated.
	"""
	cases = await medical_case_repo.get_by_guest_session(guest_session_id)
	for case in cases:
		case.user_id = user_id
		case.guest_session_id = None
	return len(cases)
