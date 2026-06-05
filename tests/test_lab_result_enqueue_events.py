from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest

from app.models.lab_result import OCRStatus
from app.models.medical_case import MedicalCase, MedicalCaseStatus
from app.schemas.lab_result import LabResultCreate, UploadRequest


pytestmark = pytest.mark.asyncio(loop_scope="session")


def _make_repo() -> MagicMock:
	repo = MagicMock(spec=[])
	repo.add = MagicMock()
	repo.commit = AsyncMock()
	repo.refresh = AsyncMock()
	repo._session = AsyncMock()
	repo._session.commit = AsyncMock()
	# Mock the dedup query: execute(...).scalar_one_or_none() → None
	mock_execute = AsyncMock()
	mock_result = MagicMock()
	mock_result.scalar_one_or_none = MagicMock(return_value=None)
	mock_execute.return_value = mock_result
	repo._session.execute = mock_execute
	return repo


def _queued_payload(case_id: str, lab_result_id: str) -> dict[str, str]:
	return {
		"event": "queued_for_processing",
		"case_id": case_id,
		"lab_result_id": lab_result_id,
		"stage": "queue",
		"status": "queued",
		"message": "Queued for processing",
	}


async def test_upload_lab_result_emits_queued_event_for_authenticated_user() -> None:
	from app.services.lab_result import upload_lab_result

	user = SimpleNamespace(id=uuid4())
	payload = UploadRequest(file={"name": "panel.jpg", "url": "https://storage.example.com/panel.jpg"})
	lab_repo = _make_repo()
	case_repo = _make_repo()
	publish_frontend = AsyncMock()

	with (
		patch("app.tasks.pipeline.run_lab_result_pipeline") as mock_task,
		patch("app.tasks.pipeline._publish_frontend_event", publish_frontend),
	):
		case, lab_result = await upload_lab_result(lab_repo, case_repo, payload, user)

	mock_task.delay.assert_called_once_with(str(lab_result.id))
	publish_frontend.assert_awaited_once()
	args = publish_frontend.await_args.args
	assert args[0] == user.id
	assert args[1] == "queued_for_processing"
	payload = args[2]
	assert payload["case_id"] == str(case.id)
	assert payload["lab_result_id"] == str(lab_result.id)
	assert payload["stage"] == "queue"
	assert payload["status"] == "queued"
	assert payload["message"] == "Queued for processing"


async def test_handle_file_upload_emits_queued_event_for_authenticated_user() -> None:
	from app.services.lab_result import handle_file_upload

	user = SimpleNamespace(id=uuid4())
	lab_repo = _make_repo()
	case_repo = _make_repo()
	publish_frontend = AsyncMock()
	file_metadata = {
		"filename": "panel.jpg",
		"mime_type": "image/jpeg",
		"file_size": 16,
		"file_url": "https://storage.example.com/panel.jpg",
	}

	with (
		patch("app.services.storage.upload_medical_file", new_callable=AsyncMock, return_value=file_metadata),
		patch("app.tasks.pipeline.run_lab_result_pipeline") as mock_task,
		patch("app.tasks.pipeline._publish_frontend_event", publish_frontend),
	):
		case, lab_result = await handle_file_upload(
			lab_repo,
			case_repo,
			b"fake-bytes",
			"panel.jpg",
			"image/jpeg",
			user,
			None,
			"http://testserver",
		)

	mock_task.delay.assert_called_once_with(str(lab_result.id))
	publish_frontend.assert_awaited_once()
	args = publish_frontend.await_args.args
	assert args[0] == user.id
	assert args[1] == "queued_for_processing"
	assert args[2]["case_id"] == str(case.id)
	assert args[2]["lab_result_id"] == str(lab_result.id)


async def test_create_lab_result_emits_queued_event_for_case_owner() -> None:
	from app.services.lab_result import create_lab_result

	user_id = uuid4()
	case = MedicalCase(id=uuid4(), user_id=user_id, status=MedicalCaseStatus.PENDING)
	lab_repo = _make_repo()
	case_repo = _make_repo()
	case_repo.get_by_id = AsyncMock(return_value=case)
	publish_frontend = AsyncMock()
	payload = LabResultCreate(
		medical_case_id=case.id,
		file={"name": "panel.jpg", "url": "https://storage.example.com/panel.jpg"},
		ocr_status=OCRStatus.PENDING,
	)

	with (
		patch("app.tasks.pipeline.run_lab_result_pipeline") as mock_task,
		patch("app.tasks.pipeline._publish_frontend_event", publish_frontend),
	):
		lab_result = await create_lab_result(lab_repo, case_repo, payload)

	mock_task.delay.assert_called_once_with(str(lab_result.id))
	publish_frontend.assert_awaited_once()
	args = publish_frontend.await_args.args
	assert args[0] == user_id
	assert args[1] == "queued_for_processing"
	assert args[2]["case_id"] == str(case.id)
	assert args[2]["lab_result_id"] == str(lab_result.id)


async def test_guest_upload_does_not_emit_queued_event() -> None:
	from app.services.lab_result import handle_file_upload

	lab_repo = _make_repo()
	case_repo = _make_repo()
	publish_frontend = AsyncMock()
	file_metadata = {
		"filename": "panel.jpg",
		"mime_type": "image/jpeg",
		"file_size": 16,
		"file_url": "https://storage.example.com/panel.jpg",
	}

	with (
		patch("app.services.storage.upload_medical_file", new_callable=AsyncMock, return_value=file_metadata),
		patch("app.tasks.pipeline.run_lab_result_pipeline") as mock_task,
		patch("app.tasks.pipeline._publish_frontend_event", publish_frontend),
	):
		await handle_file_upload(
			lab_repo,
			case_repo,
			b"fake-bytes",
			"panel.jpg",
			"image/jpeg",
			None,
			"guest-session-id",
			"http://testserver",
		)

	mock_task.delay.assert_called_once()
	publish_frontend.assert_not_awaited()