"""
Lab result processing pipeline — Celery background task.

Two-stage flow, run sequentially in a single task:
  Stage 1 — OCR:     fetch file → extract test values → update lab_result
  Stage 2 — AI:      take extracted values → generate interpretation → save

Status transitions
------------------
lab_result.ocr_status:
  PENDING → PROCESSING → COMPLETE  (or FAILED on error)

ai_interpretation.status:
  (created as) PROCESSING → COMPLETE  (or FAILED on error)

medical_case.status:
  PENDING → COMPLETE  (or FAILED on any stage error)
"""

from __future__ import annotations

import asyncio
import logging
from uuid import UUID

from celery import shared_task

logger = logging.getLogger(__name__)

PIPELINE_QUEUE = "pipeline"

# One persistent event loop per worker process.
#  Celery tasks are synchronous, but our pipeline stages are async, so we need to bridge the gap.
_worker_loop: asyncio.AbstractEventLoop | None = None


def _get_worker_loop() -> asyncio.AbstractEventLoop:
	global _worker_loop
	if _worker_loop is None or _worker_loop.is_closed():
		_worker_loop = asyncio.new_event_loop()
		asyncio.set_event_loop(_worker_loop)
	return _worker_loop


@shared_task(
	bind=True,
	name="app.tasks.pipeline.run_lab_result_pipeline",
	queue=PIPELINE_QUEUE,
	acks_late=True,
	max_retries=2,
	default_retry_delay=60,
)
def run_lab_result_pipeline(self, lab_result_id: str) -> None:  # noqa: ARG001
	"""Celery task entry point."""
	try:
		loop = _get_worker_loop()
		loop.run_until_complete(_run_pipeline(UUID(lab_result_id)))
	except Exception as exc:
		logger.exception("[pipeline] unhandled error for lab_result_id=%s", lab_result_id)
		raise self.retry(exc=exc) from exc


#  Async pipeline


async def _run_pipeline(lab_result_id: UUID) -> None:
	"""Execute the full OCR → AI pipeline for one lab result."""
	from app.db.session import AsyncSessionLocal

	async with AsyncSessionLocal() as session:
		lab_result = await _get_lab_result(session, lab_result_id)

		if lab_result is None:
			logger.warning("[pipeline] lab_result_id=%s not found — aborting", lab_result_id)
			return

		case_id: UUID = lab_result.medical_case_id
		file_url: str = (lab_result.file or {}).get("url", "")

		# Guard: storage URL must be present
		if not file_url:
			logger.error("[pipeline] lab_result_id=%s has no file URL — marking failed", lab_result_id)
			await _set_ocr_status(session, lab_result_id, "failed")
			await _set_case_status(session, case_id, "failed")
			return

		# Stage 1: OCR

		await _set_ocr_status(session, lab_result_id, "processing")
		logger.info("[pipeline] stage 1 — OCR starting for lab_result_id=%s", lab_result_id)

		try:
			from app.services.ocr import OCRExtractionError, extract_lab_values

			extracted = await extract_lab_values(file_url)
			await _set_ocr_status(session, lab_result_id, "complete", extracted_values=extracted)
			logger.info("[pipeline] stage 1 — OCR complete for lab_result_id=%s", lab_result_id)

		except OCRExtractionError as exc:
			logger.error("[pipeline] stage 1 — OCR failed for lab_result_id=%s: %s", lab_result_id, exc)
			await _set_ocr_status(session, lab_result_id, "failed")
			await _set_case_status(session, case_id, "failed")
			return

		# Stage 2: AI interpretation

		interp_id = await _create_interpretation(session, case_id)
		logger.info("[pipeline] stage 2 — AI interpretation starting, interp_id=%s", interp_id)

		try:
			from app.services.ai import InterpretationError, generate_interpretation

			interpretation = await generate_interpretation(extracted)
			await _complete_interpretation(session, interp_id, interpretation)
			await _set_case_status(session, case_id, "complete")
			logger.info("[pipeline] stage 2 — complete for lab_result_id=%s", lab_result_id)

		except InterpretationError as exc:
			logger.error("[pipeline] stage 2 — AI failed for lab_result_id=%s: %s", lab_result_id, exc)
			await _fail_interpretation(session, interp_id)
			await _set_case_status(session, case_id, "failed")


async def _get_lab_result(session, lab_result_id: UUID):  # type: ignore[no-untyped-def]
	from app.models.lab_result import LabResult

	return await session.get(LabResult, lab_result_id)


async def _set_ocr_status(session, lab_result_id: UUID, status: str, *, extracted_values=None) -> None:  # type: ignore[no-untyped-def]
	from datetime import datetime, timezone

	from app.models.lab_result import LabResult, OCRStatus

	lab_result = await session.get(LabResult, lab_result_id)
	if lab_result is None:
		return
	lab_result.ocr_status = OCRStatus(status)
	if extracted_values is not None:
		lab_result.extracted_values = extracted_values
		lab_result.ocr_completed_at = datetime.now(timezone.utc)
	await session.commit()


async def _set_case_status(session, case_id: UUID, status: str) -> None:  # type: ignore[no-untyped-def]
	from app.models.medical_case import MedicalCase, MedicalCaseStatus

	case = await session.get(MedicalCase, case_id)
	if case is None:
		return
	case.status = MedicalCaseStatus(status)
	await session.commit()


async def _create_interpretation(session, case_id: UUID) -> UUID:  # type: ignore[no-untyped-def]
	from app.models.ai_interpretation import AIInterpretation, InterpretationStatus

	interp = AIInterpretation(
		medical_case_id=case_id,
		status=InterpretationStatus.PROCESSING,
	)
	session.add(interp)
	await session.commit()
	await session.refresh(interp)
	return interp.id


async def _complete_interpretation(session, interp_id: UUID, result: dict) -> None:  # type: ignore[no-untyped-def]
	from app.models.ai_interpretation import AIInterpretation, InterpretationStatus

	interp = await session.get(AIInterpretation, interp_id)
	if interp is None:
		return
	interp.status = InterpretationStatus.COMPLETE
	interp.summary = result.get("summary")
	interp.value_breakdown = result.get("value_breakdown")
	interp.suggested_questions = result.get("suggested_questions")
	interp.risk_level = result.get("risk_level")
	interp.confidence = result.get("confidence")
	await session.commit()


async def _fail_interpretation(session, interp_id: UUID) -> None:  # type: ignore[no-untyped-def]
	from app.models.ai_interpretation import AIInterpretation, InterpretationStatus

	interp = await session.get(AIInterpretation, interp_id)
	if interp is None:
		return
	interp.status = InterpretationStatus.FAILED
	await session.commit()
