from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest

from app.models.lab_result import OCRStatus
from app.services.ai import InterpretationError
from app.services.ocr import OCRExtractionError


pytestmark = pytest.mark.asyncio(loop_scope="session")

USER_ID = uuid4()
LAB_RESULT_ID = uuid4()
CASE_ID = uuid4()
INTERP_ID = uuid4()

EXTRACTED = {
	"tests": [
		{"name": "Haemoglobin", "value": "11.2", "unit": "g/dL", "reference_range": "12.0–17.5"},
		{"name": "WBC", "value": "6.1", "unit": "x10³/µL", "reference_range": "4.0–11.0"},
	]
}

INTERPRETATION = {
	"summary": "Most values are within normal range.",
	"value_breakdown": [{"metric": "WBC", "value": "6.1", "status": "normal"}],
	"suggested_questions": ["Do I need a follow-up test?"],
	"risk_level": "moderate",
	"confidence": "high",
}


def _make_lab_result(file_url: str = "https://storage.example.com/lab.jpg") -> MagicMock:
	lab_result = MagicMock()
	lab_result.id = LAB_RESULT_ID
	lab_result.medical_case_id = CASE_ID
	lab_result.file = {"url": file_url}
	return lab_result


def _make_session() -> AsyncMock:
	session = AsyncMock()
	session.__aenter__ = AsyncMock(return_value=session)
	session.__aexit__ = AsyncMock(return_value=False)
	return session


class _FakeLock:
	def acquire(self) -> bool:
		return True

	def release(self) -> None:
		return None


class _FakeRedis:
	def lock(self, *_args, **_kwargs) -> _FakeLock:
		return _FakeLock()


def _event_types(mock: AsyncMock) -> list[str]:
	return [call.args[1] for call in mock.call_args_list]


async def test_publish_frontend_event_wraps_payload_for_event_bus() -> None:
	from app.tasks.pipeline import _publish_frontend_event

	publish = AsyncMock()
	with patch("app.tasks.pipeline._get_event_bus", new_callable=AsyncMock, return_value=SimpleNamespace(publish=publish)):
		await _publish_frontend_event(USER_ID, "ocr_completed", {"case_id": str(CASE_ID)})

	publish.assert_awaited_once_with(USER_ID, "ocr_completed", {"data": {"case_id": str(CASE_ID)}})


async def test_publish_frontend_event_ignores_guest_context() -> None:
	from app.tasks.pipeline import _publish_frontend_event

	publish = AsyncMock()
	with patch("app.tasks.pipeline._get_event_bus", new_callable=AsyncMock, return_value=SimpleNamespace(publish=publish)):
		await _publish_frontend_event(None, "processing_started", {"case_id": str(CASE_ID)})

	publish.assert_not_called()


async def test_run_pipeline_happy_path_emits_expected_frontend_sequence() -> None:
	from app.tasks.pipeline import _run_pipeline

	mock_session = _make_session()
	publish_frontend = AsyncMock()

	with (
		patch("app.db.session.AsyncSessionLocal", return_value=mock_session),
		patch("app.tasks.pipeline._get_lab_result", new_callable=AsyncMock, return_value=_make_lab_result()),
		patch("app.tasks.pipeline._get_case_user_id", new_callable=AsyncMock, return_value=USER_ID),
		patch("app.tasks.pipeline._set_ocr_status", new_callable=AsyncMock),
		patch("app.tasks.pipeline._set_case_status", new_callable=AsyncMock),
		patch("app.tasks.pipeline._create_interpretation", new_callable=AsyncMock, return_value=INTERP_ID),
		patch("app.tasks.pipeline._complete_interpretation", new_callable=AsyncMock),
		patch("app.tasks.pipeline._log_event", new_callable=AsyncMock),
		patch("app.tasks.pipeline._publish_pipeline_event", new_callable=AsyncMock),
		patch("app.tasks.pipeline._publish_frontend_event", publish_frontend),
		patch("app.services.ocr.extract_lab_values", new_callable=AsyncMock, return_value=EXTRACTED),
		patch("app.services.ai.generate_interpretation", new_callable=AsyncMock, return_value=INTERPRETATION),
		patch("app.services.llm.get_last_provider", return_value="mock-provider"),
	):
		await _run_pipeline(LAB_RESULT_ID)

	assert _event_types(publish_frontend) == [
		"processing_started",
		"ocr_started",
		"ocr_completed",
		"ai_analysis_started",
		"ai_analysis_completed",
		"ready_for_review",
	]
	first_payload = publish_frontend.call_args_list[0].args[2]
	assert first_payload["case_id"] == str(CASE_ID)
	assert first_payload["lab_result_id"] == str(LAB_RESULT_ID)
	assert first_payload["stage"] == "processing"
	assert first_payload["status"] == "processing"

	last_payload = publish_frontend.call_args_list[-1].args[2]
	assert last_payload["stage"] == "review"
	assert last_payload["status"] == "ready_for_review"


async def test_run_pipeline_ocr_failure_emits_processing_failed_and_stops() -> None:
	from app.tasks.pipeline import _run_pipeline

	mock_session = _make_session()
	publish_frontend = AsyncMock()

	with (
		patch("app.db.session.AsyncSessionLocal", return_value=mock_session),
		patch("app.tasks.pipeline._get_lab_result", new_callable=AsyncMock, return_value=_make_lab_result()),
		patch("app.tasks.pipeline._get_case_user_id", new_callable=AsyncMock, return_value=USER_ID),
		patch("app.tasks.pipeline._set_ocr_status", new_callable=AsyncMock),
		patch("app.tasks.pipeline._set_case_status", new_callable=AsyncMock),
		patch("app.tasks.pipeline._create_interpretation", new_callable=AsyncMock),
		patch("app.tasks.pipeline._log_event", new_callable=AsyncMock),
		patch("app.tasks.pipeline._publish_pipeline_event", new_callable=AsyncMock),
		patch("app.tasks.pipeline._publish_frontend_event", publish_frontend),
		patch("app.services.ocr.extract_lab_values", new_callable=AsyncMock, side_effect=OCRExtractionError("blurry scan")),
		patch("app.services.llm.get_last_provider", return_value="mock-provider"),
	):
		await _run_pipeline(LAB_RESULT_ID)

	assert _event_types(publish_frontend) == ["processing_started", "ocr_started", "processing_failed"]
	failed_payload = publish_frontend.call_args_list[-1].args[2]
	assert failed_payload["stage"] == "ocr"
	assert failed_payload["status"] == "failed"
	assert failed_payload["message"] == "blurry scan"


async def test_run_pipeline_ai_failure_emits_processing_failed() -> None:
	from app.tasks.pipeline import _run_pipeline

	mock_session = _make_session()
	publish_frontend = AsyncMock()

	with (
		patch("app.db.session.AsyncSessionLocal", return_value=mock_session),
		patch("app.tasks.pipeline._get_lab_result", new_callable=AsyncMock, return_value=_make_lab_result()),
		patch("app.tasks.pipeline._get_case_user_id", new_callable=AsyncMock, return_value=USER_ID),
		patch("app.tasks.pipeline._set_ocr_status", new_callable=AsyncMock),
		patch("app.tasks.pipeline._set_case_status", new_callable=AsyncMock),
		patch("app.tasks.pipeline._create_interpretation", new_callable=AsyncMock, return_value=INTERP_ID),
		patch("app.tasks.pipeline._fail_interpretation", new_callable=AsyncMock),
		patch("app.tasks.pipeline._log_event", new_callable=AsyncMock),
		patch("app.tasks.pipeline._publish_pipeline_event", new_callable=AsyncMock),
		patch("app.tasks.pipeline._publish_frontend_event", publish_frontend),
		patch("app.services.ocr.extract_lab_values", new_callable=AsyncMock, return_value=EXTRACTED),
		patch("app.services.ai.generate_interpretation", new_callable=AsyncMock, side_effect=InterpretationError("model timeout")),
		patch("app.services.llm.get_last_provider", return_value="mock-provider"),
	):
		await _run_pipeline(LAB_RESULT_ID)

	assert _event_types(publish_frontend) == [
		"processing_started",
		"ocr_started",
		"ocr_completed",
		"ai_analysis_started",
		"processing_failed",
	]
	failed_payload = publish_frontend.call_args_list[-1].args[2]
	assert failed_payload["stage"] == "interpretation"
	assert failed_payload["status"] == "failed"
	assert failed_payload["message"] == "model timeout"


async def test_run_lab_result_pipeline_emits_retry_scheduled_before_retry() -> None:
	from app.tasks.pipeline import run_lab_result_pipeline

	# Bind a fake Task instance with the desired attributes to the task's
	# `run` method so we can control `request` and `retry` behavior for the
	# test, then restore the original `run` afterwards.
	from types import MethodType

	orig_run = run_lab_result_pipeline.run
	try:
		class _Task:
			request = SimpleNamespace(retries=0)
			max_retries = 3
			retry = MagicMock(side_effect=RuntimeError("retry later"))

		run_lab_result_pipeline.run = MethodType(orig_run.__func__, _Task())

		publish_frontend = AsyncMock()

		with (
			patch("app.tasks.pipeline._get_redis", return_value=_FakeRedis()),
			patch("app.tasks.pipeline._run_pipeline", new_callable=AsyncMock, side_effect=RuntimeError("boom")),
			patch("app.tasks.pipeline._get_pipeline_context", new_callable=AsyncMock, return_value=(CASE_ID, USER_ID)),
			patch("app.tasks.pipeline._publish_frontend_event", publish_frontend),
		):
			with pytest.raises(RuntimeError, match="retry later"):
				run_lab_result_pipeline.run(str(LAB_RESULT_ID))
	finally:
		run_lab_result_pipeline.run = orig_run

	assert _event_types(publish_frontend) == ["retry_scheduled"]
	retry_payload = publish_frontend.call_args_list[0].args[2]
	assert retry_payload["case_id"] == str(CASE_ID)
	assert retry_payload["lab_result_id"] == str(LAB_RESULT_ID)
	assert retry_payload["attempt"] == 1


async def test_mark_pipeline_dead_emits_final_processing_failed_event() -> None:
	from app.tasks.pipeline import _mark_pipeline_dead

	mock_session = _make_session()
	lab_result = _make_lab_result()
	lab_result.ocr_status = OCRStatus.PROCESSING
	publish_frontend = AsyncMock()

	with (
		patch("app.db.session.AsyncSessionLocal", return_value=mock_session),
		patch("app.tasks.pipeline._get_lab_result", new_callable=AsyncMock, return_value=lab_result),
		patch("app.tasks.pipeline._get_case_user_id", new_callable=AsyncMock, return_value=USER_ID),
		patch("app.tasks.pipeline._set_ocr_status", new_callable=AsyncMock) as set_ocr,
		patch("app.tasks.pipeline._set_case_status", new_callable=AsyncMock) as set_case,
		patch("app.tasks.pipeline._log_event", new_callable=AsyncMock),
		patch("app.tasks.pipeline._publish_frontend_event", publish_frontend),
	):
		await _mark_pipeline_dead(LAB_RESULT_ID, "permanent failure", 4)

	set_ocr.assert_called_once_with(mock_session, LAB_RESULT_ID, "failed")
	set_case.assert_called_once_with(mock_session, CASE_ID, "failed")
	assert _event_types(publish_frontend) == ["processing_failed"]
	final_payload = publish_frontend.call_args_list[0].args[2]
	assert final_payload["stage"] == "pipeline"
	assert final_payload["status"] == "failed"
	assert final_payload["message"] == "permanent failure"