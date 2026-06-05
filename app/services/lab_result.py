from datetime import datetime, timezone
from typing import Any
from uuid import UUID

from app.core.exceptions import BadRequestError, ForbiddenError, NotFoundError, UnauthorizedError
from app.models.guest_session import GuestSession
from app.models.lab_result import LabResult, OCRStatus
from app.models.medical_case import MedicalCase, MedicalCaseStatus
from app.models.user import User
from app.repositories.chat import ChatRepository
from app.repositories.lab_result import LabResultRepository
from app.repositories.medical_case import MedicalCaseRepository
from app.schemas.lab_result import LabResultCreate, LabResultUpdate, UploadRequest
from app.services.guest_sessions import GuestSessionManager, GuestUsageAction
from app.services.websocket_chat import save_file_message, save_user_message


async def upload_lab_result(
	lab_repo: LabResultRepository,
	case_repo: MedicalCaseRepository,
	payload: UploadRequest,
	user: User | None,
	*,
	guest_session: GuestSession | None = None,
	manager: GuestSessionManager | None = None,
) -> tuple[MedicalCase, LabResult]:
	"""Create a MedicalCase + LabResult in one action and fire the pipeline.

	This is the primary upload path.  The user never needs to create a case
	explicitly — the act of uploading the file is what creates it.
	"""
	from app.tasks.pipeline import run_lab_result_pipeline

	guest_session_uuid: UUID | None = None
	if user is None:
		if guest_session is None:
			raise UnauthorizedError("Authentication or a valid guest session is required.")
		if manager is None:
			raise ForbiddenError("Guest uploads require a session manager.")
		guest_session_uuid = guest_session.id
		await manager.can_use(guest_session_uuid, GuestUsageAction.UPLOAD)

	# Create the case
	case = MedicalCase(
		user_id=user.id if user else None,
		guest_session_id=guest_session_uuid,
		status=MedicalCaseStatus.PENDING,
	)
	case_repo.add(case)
	await case_repo.commit()
	await case_repo.refresh(case)

	# Attach the lab result
	file_data = payload.file.model_dump()
	lab_result = LabResult(
		medical_case_id=case.id,
		file=file_data,
		ocr_status=OCRStatus.PENDING,
	)
	lab_repo.add(lab_result)
	await lab_repo._session.flush()

	# Create file-card chat message (same session, defer commit)
	chat_repo = ChatRepository(lab_repo._session)
	await save_file_message(
		chat_repo,
		case.id,
		file_data,
		lab_result_id=lab_result.id,
		do_commit=False,
	)

	# Single atomic commit for both LabResult + Chat message
	await lab_repo.commit()
	await lab_repo.refresh(lab_result)

	# Fire the pipeline
	run_lab_result_pipeline.delay(str(lab_result.id))

	if user is not None:
		from app.tasks.pipeline import _publish_frontend_event

		await _publish_frontend_event(
			user.id,
			"queued_for_processing",
			{
				"event": "queued_for_processing",
				"case_id": str(case.id),
				"lab_result_id": str(lab_result.id),
				"stage": "queue",
				"status": "queued",
				"message": "Queued for processing",
				"timestamp": datetime.now(timezone.utc).isoformat(),
			},
		)

	return case, lab_result


async def handle_file_upload(
	lab_repo: LabResultRepository,
	case_repo: MedicalCaseRepository,
	file: bytes,
	filename: str,
	content_type: str,
	user: User | None,
	guest_session_id: str | None,
	public_url_base: str,
) -> tuple[MedicalCase, LabResult]:
	"""Create a MedicalCase + LabResult for a file upload and enqueue the pipeline."""
	from app.services.storage import upload_medical_file
	from app.tasks.pipeline import run_lab_result_pipeline

	case = MedicalCase(
		user_id=user.id if user else None,
		guest_session_id=guest_session_id,
		status=MedicalCaseStatus.PENDING,
	)
	case_repo.add(case)
	await case_repo.commit()
	await case_repo.refresh(case)

	file_metadata = await upload_medical_file(
		file,
		filename,
		content_type,
		public_url_base,
	)

	lab_result = LabResult(
		medical_case_id=case.id,
		file={
			"name": file_metadata["filename"],
			"url": file_metadata["file_url"],
			"mime_type": file_metadata["mime_type"],
		},
		ocr_status=OCRStatus.PENDING,
	)
	lab_repo.add(lab_result)

	# Create file-card chat message (same session, defer commit)
	chat_repo = ChatRepository(lab_repo._session)
	await save_file_message(
		chat_repo,
		case.id,
		file_metadata,
		lab_result_id=lab_result.id,
		do_commit=False,
	)

	# Single atomic commit for both LabResult + Chat message
	await lab_repo.commit()
	await lab_repo.refresh(lab_result)

	run_lab_result_pipeline.delay(str(lab_result.id))

	if user is not None:
		from app.tasks.pipeline import _publish_frontend_event

		await _publish_frontend_event(
			user.id,
			"queued_for_processing",
			{
				"event": "queued_for_processing",
				"case_id": str(case.id),
				"lab_result_id": str(lab_result.id),
				"stage": "queue",
				"status": "queued",
				"message": "Queued for processing",
				"timestamp": datetime.now(timezone.utc).isoformat(),
			},
		)

	return case, lab_result


async def create_lab_result(
	lab_repo: LabResultRepository,
	case_repo: MedicalCaseRepository,
	payload: LabResultCreate,
) -> LabResult:
	"""Attach a new lab result to an existing medical case and fire the pipeline."""
	from app.tasks.pipeline import run_lab_result_pipeline  # local import avoids circular dependency at module load

	case = await case_repo.get_by_id(payload.medical_case_id)
	if case is None:
		raise NotFoundError("Medical case not found.")

	file_data = payload.file.model_dump()
	lab_result = LabResult(
		medical_case_id=payload.medical_case_id,
		file=file_data,
		ocr_status=payload.ocr_status,
	)
	lab_repo.add(lab_result)

	# Create file-card chat message (same session, defer commit)
	chat_repo = ChatRepository(lab_repo._session)
	await save_file_message(
		chat_repo,
		case.id,
		file_data,
		lab_result_id=lab_result.id,
		do_commit=False,
	)

	# Single atomic commit for both LabResult + Chat message
	await lab_repo.commit()
	await lab_repo.refresh(lab_result)

	run_lab_result_pipeline.delay(str(lab_result.id))

	if case.user_id is not None:
		from app.tasks.pipeline import _publish_frontend_event

		await _publish_frontend_event(
			case.user_id,
			"queued_for_processing",
			{
				"event": "queued_for_processing",
				"case_id": str(case.id),
				"lab_result_id": str(lab_result.id),
				"stage": "queue",
				"status": "queued",
				"message": "Queued for processing",
				"timestamp": datetime.now(timezone.utc).isoformat(),
			},
		)

	return lab_result


async def add_file_to_case(
	lab_repo: LabResultRepository,
	case_repo: MedicalCaseRepository,
	case_id: UUID,
	file: bytes,
	filename: str,
	content_type: str,
	public_url_base: str,
	*,
	user_id: UUID | None = None,
	note: str | None = None,
) -> LabResult:
	"""Upload a file and attach it as a new lab result to an existing medical case.

	If `note` is provided it is saved as a PATIENT chat message immediately after
	the file-card, giving the AI context about what the user wants to know.
	"""
	from app.services.storage import upload_medical_file
	from app.tasks.pipeline import run_lab_result_pipeline

	case = await case_repo.get_by_id(case_id)
	if case is None:
		raise NotFoundError("Medical case not found.")

	existing_count = await lab_repo.count_by_case(case_id)
	if existing_count >= 3:
		raise BadRequestError("A medical case cannot have more than 3 lab result uploads.")

	file_metadata = await upload_medical_file(file, filename, content_type, public_url_base)

	file_data = {
		"name": file_metadata["filename"],
		"url": file_metadata["file_url"],
		"mime_type": file_metadata["mime_type"],
	}
	lab_result = LabResult(
		medical_case_id=case_id,
		file=file_data,
		ocr_status=OCRStatus.PENDING,
	)
	lab_repo.add(lab_result)

	# Create file-card chat message (same session, defer commit)
	chat_repo = ChatRepository(lab_repo._session)
	await save_file_message(
		chat_repo,
		case_id,
		file_data,
		lab_result_id=lab_result.id,
		do_commit=False,
	)

	# If the user attached a note, save it as a patient message in the same transaction
	if note:
		await save_user_message(chat_repo, case_id, user_id, note, do_commit=False)

	# Single atomic commit for LabResult + file-card + optional note
	await lab_repo.commit()
	await lab_repo.refresh(lab_result)

	run_lab_result_pipeline.delay(str(lab_result.id))

	if case.user_id is not None:
		from app.tasks.pipeline import _publish_frontend_event

		await _publish_frontend_event(
			case.user_id,
			"queued_for_processing",
			{
				"event": "queued_for_processing",
				"case_id": str(case.id),
				"lab_result_id": str(lab_result.id),
				"stage": "queue",
				"status": "queued",
				"message": "Queued for processing",
				"timestamp": datetime.now(timezone.utc).isoformat(),
			},
		)

	return lab_result


async def get_lab_result(
	lab_repo: LabResultRepository,
	result_id: UUID,
) -> LabResult:
	result = await lab_repo.get_by_id(result_id)
	if result is None:
		raise NotFoundError("Lab result not found.")
	return result


async def list_lab_results_for_case(
	lab_repo: LabResultRepository,
	medical_case_id: UUID,
	*,
	offset: int = 0,
	limit: int = 50,
) -> list[LabResult]:
	return await lab_repo.list_by_case(medical_case_id, offset=offset, limit=limit)


async def update_lab_result(
	lab_repo: LabResultRepository,
	result_id: UUID,
	payload: LabResultUpdate,
) -> LabResult:
	"""Partially update a lab result (typically after OCR completes)."""
	lab_result = await get_lab_result(lab_repo, result_id)
	if payload.ocr_status is not None:
		lab_result.ocr_status = payload.ocr_status
	if payload.extracted_values is not None:
		lab_result.extracted_values = payload.extracted_values
	if payload.ocr_completed_at is not None:
		lab_result.ocr_completed_at = payload.ocr_completed_at
	await lab_repo.commit()
	await lab_repo.refresh(lab_result)
	return lab_result


async def mark_ocr_complete(
	lab_repo: LabResultRepository,
	result_id: UUID,
	extracted_values: dict[str, Any],
) -> LabResult:
	"""Convenience: mark OCR as complete and store the extracted values."""
	lab_result = await get_lab_result(lab_repo, result_id)
	lab_result.ocr_status = OCRStatus.COMPLETE
	lab_result.extracted_values = extracted_values
	lab_result.ocr_completed_at = datetime.now(timezone.utc)
	await lab_repo.commit()
	await lab_repo.refresh(lab_result)
	return lab_result
