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
import inspect
import logging
import threading
from typing import Any, Coroutine
from uuid import UUID

import redis as redis_sync
from celery import shared_task

from app.core.celery_app import PIPELINE_DLQ_QUEUE, PIPELINE_QUEUE

logger = logging.getLogger(__name__)
_LOCK_TTL_SECONDS = 300

# Valid forward transitions — FAILED is reachable from any state.
_VALID_OCR_TRANSITIONS: dict[str, set[str]] = {
	"pending": {"processing", "failed"},
	"processing": {"complete", "failed"},
	"complete": {"failed"},
	"failed": {"failed"},
}

_VALID_CASE_TRANSITIONS: dict[str, set[str]] = {
	"pending": {"processing", "complete", "failed"},
	"processing": {"complete", "failed"},
	"complete": {"failed"},
	"failed": {"failed"},
}

# One persistent event loop per worker process.
_worker_loop: asyncio.AbstractEventLoop | None = None

# One sync Redis client per worker process — reused across task calls.
_redis_client: redis_sync.Redis | None = None


def _get_worker_loop() -> asyncio.AbstractEventLoop:
	global _worker_loop
	if _worker_loop is None or _worker_loop.is_closed():
		_worker_loop = asyncio.new_event_loop()
		asyncio.set_event_loop(_worker_loop)
	return _worker_loop


def _run_coro_in_thread(coro: Coroutine[Any, Any, Any]) -> Any:
	"""Run the given coroutine in a fresh event loop on a background thread.
	This is used when the current thread already has a running event loop
	(such as during pytest-asyncio test runs) so we avoid "event loop is
	already running" errors by executing the coroutine on another thread.
	"""
	result: dict = {}

	def _target():
		try:
			result["value"] = asyncio.run(coro)
		except Exception as e:  # capture to re-raise in caller thread
			result["exc"] = e

	thr = threading.Thread(target=_target)
	thr.start()
	thr.join()
	if "exc" in result:
		raise result["exc"]
	return result.get("value")


def _get_redis() -> redis_sync.Redis:
	global _redis_client
	if _redis_client is None:
		from app.core.config import get_settings

		settings = get_settings()
		_redis_client = redis_sync.Redis.from_url(
			settings.CELERY_BROKER_URL,
			decode_responses=True,
		)
	return _redis_client


_event_bus_instance: Any | None = None


async def _get_event_bus() -> Any:
	"""Return the per-process EventBus, connecting on first call."""
	global _event_bus_instance
	if _event_bus_instance is None:
		from app.core.config import get_settings
		from app.services.events import EventBus

		settings = get_settings()
		bus = EventBus(settings.CELERY_BROKER_URL)
		await bus.connect()
		_event_bus_instance = bus
	return _event_bus_instance


@shared_task(
	bind=True,
	name="app.tasks.pipeline.run_lab_result_pipeline",
	queue=PIPELINE_QUEUE,
	acks_late=True,
	max_retries=3,
	retry_backoff=True,
	retry_backoff_max=600,
	retry_jitter=True,
)
def run_lab_result_pipeline(self, lab_result_id: str, *args, **kwargs) -> None:
	"""Celery task entry point.

	Retry policy: exponential backoff with jitter, max 3 retries.
	If all retries are exhausted the task is forwarded to the dead-letter
	queue (pipeline.dlq)
	"""
	lock_key = f"pipeline:lock:{lab_result_id}"
	lock = _get_redis().lock(
		lock_key,
		timeout=_LOCK_TTL_SECONDS,
		blocking=False,
	)

	if not lock.acquire():
		logger.info(
			"[pipeline] lock already held for lab_result_id=%s — skipping duplicate",
			lab_result_id,
		)
		return

	try:
		# If the test harness or caller already has a running event loop in this
		# thread (eg. pytest-asyncio), running another loop with
		# run_until_complete will fail. Detect that case and execute the
		# coroutine in a background thread instead.
		# Check if a loop is already running in this thread. Only the call to
		# `asyncio.get_running_loop()` should be used to detect that; do not let
		# RuntimeErrors raised by the coroutine itself fall into this branch.
		try:
			asyncio.get_running_loop()
		except RuntimeError:
			loop = _get_worker_loop()
			loop.run_until_complete(_run_pipeline(UUID(lab_result_id), attempt=self.request.retries))
		else:
			_run_coro_in_thread(_run_pipeline(UUID(lab_result_id), attempt=self.request.retries))
	except Exception as exc:
		try:
			# Obtain pipeline context; run in background thread if needed.
			try:
				asyncio.get_running_loop()
			except RuntimeError:
				context = _get_worker_loop().run_until_complete(_get_pipeline_context(UUID(lab_result_id)))
			else:
				context = _run_coro_in_thread(_get_pipeline_context(UUID(lab_result_id)))
			if context is not None and self.request.retries < self.max_retries:
				case_id, user_id = context
				from datetime import datetime, timezone

				# Publish retry scheduled frontend event without touching DB.
				try:
					asyncio.get_running_loop()
				except RuntimeError:
					_get_worker_loop().run_until_complete(
						_publish_frontend_event(
							user_id,
							"retry_scheduled",
							{
								"event": "retry_scheduled",
								"case_id": str(case_id),
								"lab_result_id": str(lab_result_id),
								"stage": "retry",
								"status": "processing",
								"message": "Retry scheduled",
								"attempt": self.request.retries + 1,
								"timestamp": datetime.now(timezone.utc).isoformat(),
							},
						)
					)
				else:
					_run_coro_in_thread(
						_publish_frontend_event(
							user_id,
							"retry_scheduled",
							{
								"event": "retry_scheduled",
								"case_id": str(case_id),
								"lab_result_id": str(lab_result_id),
								"stage": "retry",
								"status": "processing",
								"message": "Retry scheduled",
								"attempt": self.request.retries + 1,
								"timestamp": datetime.now(timezone.utc).isoformat(),
							},
						)
					)
		except Exception:
			pass
		logger.exception(
			"[pipeline] error on attempt %d/%d for lab_result_id=%s",
			self.request.retries + 1,
			self.max_retries + 1,
			lab_result_id,
		)
		if self.request.retries >= self.max_retries:
			logger.error(
				"[pipeline] all retries exhausted for lab_result_id=%s — sending to DLQ",
				lab_result_id,
			)
			dead_letter_pipeline.delay(
				lab_result_id=lab_result_id,
				error=str(exc),
				attempts=self.request.retries + 1,
			)
			return
		# Call the task's retry handler and let Celery raise the control-flow
		# exception (tests should patch `self.retry` to raise when needed).
		self.retry(exc=exc)
	finally:
		try:
			lock.release()
		except Exception:
			pass


#  Async pipeline


@shared_task(
	name="app.tasks.pipeline.dead_letter_pipeline",
	queue=PIPELINE_DLQ_QUEUE,
	acks_late=True,
	max_retries=0,
)
def dead_letter_pipeline(
	lab_result_id: str,
	error: str,
	attempts: int,
) -> None:
	"""Handle a pipeline task that has exhausted all retries.

	Ensures the case is marked FAILED in the DB and records the failure
	"""
	logger.error(
		"[dlq] pipeline permanently failed — lab_result_id=%s, attempts=%d, error=%r",
		lab_result_id,
		attempts,
		error,
	)
	# Run the async cleanup in a safe way even if a loop is already running
	try:
		asyncio.get_running_loop()
	except RuntimeError:
		loop = _get_worker_loop()
		loop.run_until_complete(_mark_pipeline_dead(UUID(lab_result_id), error, attempts))
	else:
		_run_coro_in_thread(_mark_pipeline_dead(UUID(lab_result_id), error, attempts))


async def _mark_pipeline_dead(
	lab_result_id: UUID,
	error: str,
	attempts: int,
) -> None:
	"""
	Ensures the lab result and case are in FAILED state after DLQ dispatch.
	"""
	from app.db.session import AsyncSessionLocal
	from app.models.lab_result import OCRStatus

	async with AsyncSessionLocal() as session:
		lab_result = await _get_lab_result(session, lab_result_id)

		if lab_result is None:
			logger.warning("[dlq] lab_result_id=%s not found — nothing to mark", lab_result_id)
			return

		case_id: UUID = lab_result.medical_case_id

		from datetime import datetime, timezone

		if lab_result.ocr_status not in (OCRStatus.COMPLETE, OCRStatus.FAILED):
			await _set_ocr_status(session, lab_result_id, "failed")

		await _set_case_status(session, case_id, "failed")

		await _log_event(
			session,
			lab_result_id,
			"PIPELINE_DEAD_LETTERED",
			error=error,
			attempt=attempts,
		)

		# Publish final frontend failure event so clients can update UI (ephemeral)
		try:
			user_id = await _get_case_user_id(session, case_id)
			await _publish_frontend_event(
				user_id,
				"processing_failed",
				{
					"case_id": str(case_id),
					"lab_result_id": str(lab_result_id),
					"stage": "pipeline",
					"status": "failed",
					"message": error,
					"timestamp": datetime.now(timezone.utc).isoformat(),
				},
			)
		except Exception:
			pass

		logger.error(
			"[dlq] marked lab_result_id=%s FAILED after %d attempts: %s",
			lab_result_id,
			attempts,
			error,
		)


async def _get_pipeline_context(lab_result_id: UUID) -> tuple[UUID, UUID | None] | None:
	from app.db.session import AsyncSessionLocal

	async with AsyncSessionLocal() as session:
		lab_result = await _get_lab_result(session, lab_result_id)
		if lab_result is None:
			return None
		return lab_result.medical_case_id, await _get_case_user_id(session, lab_result.medical_case_id)


async def _run_pipeline(lab_result_id: UUID, attempt: int = 0) -> None:
	"""Execute the full OCR → AI pipeline for one lab result."""
	import time
	from datetime import datetime, timezone

	from app.db.session import AsyncSessionLocal
	from app.services.llm import get_last_provider

	async with AsyncSessionLocal() as session:
		lab_result = await _get_lab_result(session, lab_result_id)

		if lab_result is None:
			logger.warning("[pipeline] lab_result_id=%s not found — aborting", lab_result_id)
			return

		case_id: UUID = lab_result.medical_case_id
		file_url: str = (lab_result.file or {}).get("url", "")

		user_id: UUID | None = await _get_case_user_id(session, case_id)

		try:
			from datetime import datetime, timezone

			await _publish_frontend_event(
				user_id,
				"processing_started",
				{
					"event": "processing_started",
					"case_id": str(case_id),
					"lab_result_id": str(lab_result_id),
					"stage": "processing",
					"status": "processing",
					"message": "Pipeline started",
					"timestamp": datetime.now(timezone.utc).isoformat(),
				},
			)
		except Exception:
			pass

		await _log_event(session, lab_result_id, "PIPELINE_STARTED", attempt=attempt)

		# Guard: storage URL must be present
		if not file_url:
			logger.error("[pipeline] lab_result_id=%s has no file URL — marking failed", lab_result_id)
			await _set_ocr_status(session, lab_result_id, "failed")
			await _set_case_status(session, case_id, "failed")
			await _log_event(session, lab_result_id, "PIPELINE_FAILED", error="No file URL present")
			await _publish_pipeline_event(session, user_id, "interpretation_failed", {"case_id": str(case_id)}, case_id)
			# Emit frontend transient failure
			try:
				await _publish_frontend_event(
					user_id,
					"processing_failed",
					{
						"case_id": str(case_id),
						"lab_result_id": str(lab_result_id),
						"stage": "pipeline",
						"status": "failed",
						"message": "No file URL present",
						"timestamp": datetime.now(timezone.utc).isoformat(),
					},
				)
			except Exception:
				pass
			return

		# Stage 1: OCR
		await _set_ocr_status(session, lab_result_id, "processing")
		try:
			from datetime import datetime, timezone

			await _publish_frontend_event(
				user_id,
				"ocr_started",
				{
					"event": "ocr_started",
					"case_id": str(case_id),
					"lab_result_id": str(lab_result_id),
					"stage": "ocr",
					"status": "processing",
					"message": "OCR started",
					"timestamp": datetime.now(timezone.utc).isoformat(),
				},
			)
		except Exception:
			pass
		await _log_event(
			session,
			lab_result_id,
			"OCR_STARTED",
			status_before="pending",
			status_after="processing",
		)

		ocr_start = time.monotonic()
		try:
			from app.services.ocr import OCRExtractionError, extract_lab_values

			extracted = await extract_lab_values(file_url)
			ocr_ms = int((time.monotonic() - ocr_start) * 1000)

			await _set_ocr_status(session, lab_result_id, "complete", extracted_values=extracted)
			# Emit frontend progress: OCR completed
			try:
				from datetime import datetime, timezone

				await _publish_frontend_event(
					user_id,
					"ocr_completed",
					{
						"event": "ocr_completed",
						"case_id": str(case_id),
						"lab_result_id": str(lab_result_id),
						"stage": "ocr",
						"status": "complete",
						"message": "OCR completed",
						"timestamp": datetime.now(timezone.utc).isoformat(),
					},
				)
			except Exception:
				pass
			await _log_event(
				session,
				lab_result_id,
				"OCR_COMPLETE",
				status_before="processing",
				status_after="complete",
				duration_ms=ocr_ms,
				provider=get_last_provider(),
				attempt=attempt,
			)

		except OCRExtractionError as exc:
			ocr_ms = int((time.monotonic() - ocr_start) * 1000)
			logger.error("[pipeline] stage 1 — OCR failed for lab_result_id=%s: %s", lab_result_id, exc)
			await _set_ocr_status(session, lab_result_id, "failed")
			# Emit frontend progress: OCR failed (transient UI event)
			try:
				await _publish_frontend_event(
					user_id,
					"processing_failed",
					{
						"case_id": str(case_id),
						"lab_result_id": str(lab_result_id),
						"stage": "ocr",
						"status": "failed",
						"message": str(exc),
						"timestamp": datetime.now(timezone.utc).isoformat(),
					},
				)
			except Exception:
				pass
			await _set_case_status(session, case_id, "failed")
			await _log_event(
				session,
				lab_result_id,
				"OCR_FAILED",
				status_before="processing",
				status_after="failed",
				duration_ms=ocr_ms,
				provider=get_last_provider(),
				attempt=attempt,
				error=str(exc),
			)
			await _publish_pipeline_event(session, user_id, "interpretation_failed", {"case_id": str(case_id)}, case_id)
			return

		# Stage 2: AI interpretation
		interp_id = await _create_interpretation(session, case_id)
		await _log_event(
			session,
			lab_result_id,
			"AI_STARTED",
			status_before="pending",
			status_after="processing",
		)
		# Emit frontend progress: AI analysis started
		try:
			from datetime import datetime, timezone

			await _publish_frontend_event(
				user_id,
				"ai_analysis_started",
				{
					"event": "ai_analysis_started",
					"case_id": str(case_id),
					"lab_result_id": str(lab_result_id),
					"stage": "interpretation",
					"status": "processing",
					"message": "AI analysis started",
					"timestamp": datetime.now(timezone.utc).isoformat(),
				},
			)
		except Exception:
			pass

		ai_start = time.monotonic()
		try:
			from app.services.ai import InterpretationError, generate_interpretation

			interpretation = await generate_interpretation(extracted)
			ai_ms = int((time.monotonic() - ai_start) * 1000)

			await _complete_interpretation(session, interp_id, interpretation)
			await _set_case_status(session, case_id, "complete")
			await _log_event(
				session,
				lab_result_id,
				"AI_COMPLETE",
				status_before="processing",
				status_after="complete",
				duration_ms=ai_ms,
				provider=get_last_provider(),
				attempt=attempt,
			)
			# Emit frontend progress: AI analysis complete and ready for review
			try:
				from datetime import datetime, timezone

				await _publish_frontend_event(
					user_id,
					"ai_analysis_completed",
					{
						"event": "ai_analysis_completed",
						"case_id": str(case_id),
						"lab_result_id": str(lab_result_id),
						"stage": "interpretation",
						"status": "complete",
						"message": "AI analysis complete",
						"timestamp": datetime.now(timezone.utc).isoformat(),
					},
				)
				await _publish_frontend_event(
					user_id,
					"ready_for_review",
					{
						"event": "ready_for_review",
						"case_id": str(case_id),
						"lab_result_id": str(lab_result_id),
						"stage": "review",
						"status": "ready_for_review",
						"message": "Ready for review",
						"timestamp": datetime.now(timezone.utc).isoformat(),
					},
				)
			except Exception:
				pass
			await _publish_pipeline_event(session, user_id, "interpretation_ready", {"case_id": str(case_id)}, case_id)

		except InterpretationError as exc:
			ai_ms = int((time.monotonic() - ai_start) * 1000)
			logger.error("[pipeline] stage 2 — AI failed for lab_result_id=%s: %s", lab_result_id, exc)
			await _fail_interpretation(session, interp_id)
			await _set_case_status(session, case_id, "failed")
			# Emit frontend progress: AI failed
			try:
				await _publish_frontend_event(
					user_id,
					"processing_failed",
					{
						"case_id": str(case_id),
						"lab_result_id": str(lab_result_id),
						"stage": "interpretation",
						"status": "failed",
						"message": str(exc),
						"timestamp": datetime.now(timezone.utc).isoformat(),
					},
				)
			except Exception:
				pass
			await _log_event(
				session,
				lab_result_id,
				"AI_FAILED",
				status_before="processing",
				status_after="failed",
				duration_ms=ai_ms,
				provider=get_last_provider(),
				attempt=attempt,
				error=str(exc),
			)
			await _publish_pipeline_event(session, user_id, "interpretation_failed", {"case_id": str(case_id)}, case_id)


async def _get_lab_result(session, lab_result_id: UUID):  # type: ignore[no-untyped-def]
	from app.models.lab_result import LabResult

	return await session.get(LabResult, lab_result_id)


async def _get_case_user_id(session, case_id: UUID) -> UUID | None:  # type: ignore[no-untyped-def]
	"""Return the user_id for a case, or None for guest cases."""
	from app.models.medical_case import MedicalCase

	case = await session.get(MedicalCase, case_id)
	return case.user_id if case else None


async def _log_event(
	session,
	lab_result_id: UUID,
	event: str,
	*,
	status_before: str | None = None,
	status_after: str | None = None,
	provider: str | None = None,
	duration_ms: int | None = None,
	attempt: int | None = None,
	error: str | None = None,
) -> None:
	"""Write a pipeline audit event. Delegates to PipelineAuditLogRepository."""
	from app.repositories.pipeline_audit_log import _log_event as _write_audit_log

	await _write_audit_log(
		session,
		lab_result_id=lab_result_id,
		event=event,
		status_before=status_before,
		status_after=status_after,
		provider=provider,
		duration_ms=duration_ms,
		attempt=attempt,
		error=error,
	)


async def _publish_pipeline_event(
	session,
	user_id: UUID | None,
	event_type: str,
	payload: dict,
	case_id: UUID | None = None,
) -> None:
	"""Persist a pipeline notification and publish it if the user wants completion alerts."""
	if user_id is None:
		return  # guest case — no event to publish

	from app.models.notification import Notification, NotificationType
	from app.models.user import User
	from app.repositories.notification import NotificationRepository

	user = await session.get(User, user_id)
	if user is None or not user.notify_on_complete:
		return

	notif_type = NotificationType(event_type)
	notif_repo = NotificationRepository(session)
	notification = await notif_repo.get_by_user_case_and_type(user_id, case_id, notif_type)
	# Test doubles may return awaitables from mocked repository/session paths.
	if inspect.isawaitable(notification):
		notification = await notification
	if notification is None:
		notification = Notification(
			user_id=user_id,
			medical_case_id=case_id,
			type=notif_type,
			title=(
				"Interpretation ready"
				if notif_type == NotificationType.INTERPRETATION_READY
				else "Interpretation failed"
			),
			message=(
				{"text": "Your interpretation is ready."}
				if notif_type == NotificationType.INTERPRETATION_READY
				else {"text": "Your interpretation failed."}
			),
			data=payload,
		)
		session.add(notification)
		await session.commit()
		await session.refresh(notification)

	payload_with_id = {**payload, "notification_id": str(notification.id)}
	try:
		bus = await _get_event_bus()
		await bus.publish(user_id, event_type, payload_with_id)
		logger.debug("[events] published %s for user_id=%s", event_type, user_id)
	except Exception as exc:
		logger.warning(
			"[events] failed to publish %s for user_id=%s: %s",
			event_type,
			user_id,
			exc,
		)


async def _publish_frontend_event(user_id: UUID | None, event_type: str, payload: dict) -> None:
	"""Publish an ephemeral frontend event over the EventBus. This does NOT persist
	anything to the database and is intended for UI-only progress updates.
	"""
	if user_id is None:
		return
	try:
		bus = await _get_event_bus()
		# Wrap payload under a `data` key to keep the EventBus payload shape simple.
		await bus.publish(user_id, event_type, {"data": payload})
		logger.debug("[events] published frontend event %s for user_id=%s", event_type, user_id)
	except Exception as exc:
		logger.warning(
			"[events] failed to publish frontend event %s for user_id=%s: %s",
			event_type,
			user_id,
			exc,
		)


async def _set_ocr_status(session, lab_result_id: UUID, status: str, *, extracted_values=None) -> None:  # type: ignore[no-untyped-def]
	from datetime import datetime, timezone

	from app.models.lab_result import LabResult, OCRStatus

	lab_result = await session.get(LabResult, lab_result_id)
	if lab_result is None:
		return

	current = lab_result.ocr_status.value
	if status not in _VALID_OCR_TRANSITIONS.get(current, set()):
		logger.warning("Invalid status transition: %s → %s for lab_result %s", current, status, lab_result_id)
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

	current = case.status.value
	if status not in _VALID_CASE_TRANSITIONS.get(current, set()):
		logger.warning("Invalid status transition: %s → %s for case %s", current, status, case_id)
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
