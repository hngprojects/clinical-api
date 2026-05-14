from datetime import datetime, timezone
from typing import Any
from uuid import UUID

from app.core.exceptions import NotFoundError
from app.models.lab_result import LabResult, OCRStatus
from app.models.medical_case import MedicalCase, MedicalCaseStatus
from app.models.user import User
from app.repositories.lab_result import LabResultRepository
from app.repositories.medical_case import MedicalCaseRepository
from app.schemas.lab_result import LabResultCreate, LabResultUpdate, UploadRequest


async def upload_lab_result(
	lab_repo: LabResultRepository,
	case_repo: MedicalCaseRepository,
	payload: UploadRequest,
	user: User | None,
) -> tuple[MedicalCase, LabResult]:
	"""Create a MedicalCase + LabResult in one action and fire the pipeline.

	This is the primary upload path.  The user never needs to create a case
	explicitly — the act of uploading the file is what creates it.
	"""
	from app.tasks.pipeline import run_lab_result_pipeline

	# Create the case
	case = MedicalCase(
		user_id=user.id if user else None,
		guest_session_id=payload.guest_session_id if not user else None,
		status=MedicalCaseStatus.PENDING,
	)
	case_repo.add(case)
	await case_repo.commit()
	await case_repo.refresh(case)

	# Attach the lab result
	lab_result = LabResult(
		medical_case_id=case.id,
		file=payload.file.model_dump(),
		ocr_status=OCRStatus.PENDING,
	)
	lab_repo.add(lab_result)
	await lab_repo.commit()
	await lab_repo.refresh(lab_result)

	# Fire the pipeline
	run_lab_result_pipeline.delay(str(lab_result.id))

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

	lab_result = LabResult(
		medical_case_id=payload.medical_case_id,
		file=payload.file.model_dump(),
		ocr_status=payload.ocr_status,
	)
	lab_repo.add(lab_result)
	await lab_repo.commit()
	await lab_repo.refresh(lab_result)

	run_lab_result_pipeline.delay(str(lab_result.id))

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
