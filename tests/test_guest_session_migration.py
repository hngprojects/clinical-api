"""Phase 4: guest session migration on verify-otp and service-level migration."""

from __future__ import annotations

import uuid
from unittest.mock import MagicMock, patch

import pytest
from httpx import AsyncClient
from sqlalchemy import select

from app.db.session import AsyncSessionLocal
from app.models.chat import Chat, SenderType
from app.models.medical_case import MedicalCase, MedicalCaseStatus
from app.models.user import UserRole
from app.models.user import User
from app.repositories.chat import ChatRepository
from app.repositories.medical_case import MedicalCaseRepository
from app.services.auth.tokens import create_access_token
from app.services.guest import create_guest_session, migrate_guest_session_to_user

pytestmark = pytest.mark.asyncio(loop_scope="session")

API = "/api/v1"
PIPELINE_TASK = "app.tasks.pipeline.run_lab_result_pipeline"
_UPLOAD = {
	"file": {"name": "panel.jpg", "url": "https://storage.example.com/panel.jpg"},
}


async def test_migrate_guest_session_links_cases_and_chats() -> None:
	session_info = await create_guest_session()
	user_id = uuid.uuid4()
	case_id = uuid.uuid4()
	chat_id = uuid.uuid4()

	async with AsyncSessionLocal() as db:
		db.add(
			User(
				id=user_id,
				email=f"migrate_{uuid.uuid4().hex[:8]}@clinsights.dev",
				first_name="M",
				last_name="User",
				role=UserRole.PATIENT,
				is_active=True,
				is_email_verified=False,
			)
		)
		db.add(
			MedicalCase(
				id=case_id,
				user_id=None,
				guest_session_id=uuid.UUID(session_info.guest_session_id),
				status=MedicalCaseStatus.PENDING,
			)
		)
		db.add(
			Chat(
				id=chat_id,
				user_id=None,
				medical_case_id=case_id,
				sender_type=SenderType.PATIENT,
				content={"text": "Hello"},
			)
		)
		await db.commit()

	async with AsyncSessionLocal() as db:
		case_repo = MedicalCaseRepository(db)
		chat_repo = ChatRepository(db)
		result = await migrate_guest_session_to_user(
			case_repo,
			chat_repo,
			guest_session_id=session_info.guest_session_id,
			user_id=user_id,
		)

	assert result.cases_migrated == 1
	assert result.chats_updated == 1

	async with AsyncSessionLocal() as db:
		case = await db.get(MedicalCase, case_id)
		chat = await db.get(Chat, chat_id)
		assert case is not None
		assert case.user_id == user_id
		assert case.guest_session_id is None
		assert chat is not None
		assert chat.user_id == user_id

	token, _ = create_access_token(user_id)
	async with AsyncSessionLocal() as db:
		await db.delete(await db.get(User, user_id))
		await db.commit()


async def test_verify_otp_migrates_guest_case(client: AsyncClient) -> None:
	guest = await create_guest_session()
	mock_task = MagicMock()

	with patch(PIPELINE_TASK, mock_task):
		upload = await client.post(
			f"{API}/upload",
			json={**_UPLOAD, "guest_session_id": guest.guest_session_id},
		)
	assert upload.status_code == 201
	case_id = upload.json()["data"]["case_id"]

	email = f"guest_mig_{uuid.uuid4().hex[:8]}@clinsights.dev"
	with patch("app.api.v1.endpoints.auth.send_otp_email_task.delay") as mock_delay:
		signup = await client.post(
			f"{API}/auth/signup",
			json={
				"first_name": "Guest",
				"last_name": "Migrate",
				"email": email,
				"password": "Password123!",
				"confirm_password": "Password123!",
			},
		)
		otp_code = mock_delay.call_args.kwargs["code"]
	assert signup.status_code == 201

	async with AsyncSessionLocal() as session:
		user = (await session.execute(select(User).where(User.email == email))).scalar_one()

	verify = await client.post(
		f"{API}/auth/verify-otp",
		json={
			"email": email,
			"code": otp_code,
			"guest_session_id": guest.guest_session_id,
		},
	)
	assert verify.status_code == 200
	access_token = verify.json()["data"]["access_token"]

	case_resp = await client.get(
		f"{API}/cases/{case_id}",
		headers={"Authorization": f"Bearer {access_token}"},
	)
	assert case_resp.status_code == 200
	assert case_resp.json()["data"]["user_id"] == str(user.id)
	assert case_resp.json()["data"]["guest_session_id"] is None

	async with AsyncSessionLocal() as session:
		await session.delete(user)
		await session.commit()
