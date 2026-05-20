"""Guest migration idempotency under row lock."""

from __future__ import annotations

import uuid

import pytest

from app.db.session import AsyncSessionLocal
from app.models.medical_case import MedicalCase, MedicalCaseStatus
from app.models.user import User, UserRole
from app.repositories.chat import ChatRepository
from app.repositories.guest_session import GuestSessionRepository
from app.repositories.medical_case import MedicalCaseRepository
from app.services.guest import create_guest_session
from app.services.guest_sessions import GuestSessionManager

pytestmark = pytest.mark.asyncio(loop_scope="session")


async def test_migrate_twice_is_idempotent() -> None:
	guest = await create_guest_session()
	user_id = uuid.uuid4()
	case_id = uuid.uuid4()

	async with AsyncSessionLocal() as db:
		db.add(
			User(
				id=user_id,
				email=f"idempotent_{uuid.uuid4().hex[:8]}@clinsights.dev",
				first_name="I",
				last_name="User",
				role=UserRole.PATIENT,
				is_active=True,
				is_email_verified=True,
			)
		)
		db.add(
			MedicalCase(
				id=case_id,
				user_id=None,
				guest_session_id=uuid.UUID(guest.guest_session_id),
				status=MedicalCaseStatus.PENDING,
			)
		)
		await db.commit()

	async with AsyncSessionLocal() as db:
		manager = GuestSessionManager(GuestSessionRepository(db))
		case_repo = MedicalCaseRepository(db)
		chat_repo = ChatRepository(db)
		first = await manager.migrate(uuid.UUID(guest.guest_session_id), user_id, case_repo, chat_repo)
		second = await manager.migrate(uuid.UUID(guest.guest_session_id), user_id, case_repo, chat_repo)

	assert first.cases_migrated == 1
	assert second.cases_migrated == 0

	async with AsyncSessionLocal() as db:
		case = await db.get(MedicalCase, case_id)
		assert case is not None
		assert case.user_id == user_id
		user = await db.get(User, user_id)
		if user:
			await db.delete(user)
			await db.commit()
