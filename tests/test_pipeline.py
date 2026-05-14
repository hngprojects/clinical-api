"""
Unit tests for the lab result processing pipeline.

Strategy:
- All async DB helpers and external service calls are mocked so tests run
  without a real database or OpenAI key.
- We test the async _run_pipeline() coroutine directly rather than the
  Celery task wrapper — this keeps tests framework-agnostic and fast.
- Each test asserts on the sequence of status writes rather than on return
  values, because the pipeline is side-effect-driven.
"""

from __future__ import annotations

import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

pytestmark = pytest.mark.asyncio(loop_scope="session")

from app.models.lab_result import OCRStatus
from app.models.medical_case import MedicalCaseStatus
from app.services.ocr import OCRExtractionError
from app.services.ai import InterpretationError


LAB_RESULT_ID = uuid.uuid4()
CASE_ID = uuid.uuid4()
INTERP_ID = uuid.uuid4()

EXTRACTED = {
    "tests": [
        {"name": "Haemoglobin", "value": "11.2", "unit": "g/dL", "reference_range": "12.0–17.5"},
        {"name": "WBC", "value": "6.1", "unit": "x10³/µL", "reference_range": "4.0–11.0"},
    ]
}

INTERPRETATION = {
    "summary": "Most values are within normal range. This is not a medical diagnosis. Please consult a qualified healthcare professional for personalised advice.",
    "value_breakdown": [
        {"metric": "Haemoglobin", "value": "11.2", "unit": "g/dL", "status": "caution"},
        {"metric": "WBC", "value": "6.1", "unit": "x10³/µL", "status": "normal"},
    ],
    "suggested_questions": [
        "Should I be concerned about my haemoglobin level?",
        "What lifestyle changes could improve my blood count?",
        "Do I need a follow-up test?",
    ],
    "risk_level": "moderate",
    "confidence": "high",
}


def _make_lab_result(file_url: str = "https://storage.example.com/lab.jpg") -> MagicMock:
    lr = MagicMock()
    lr.id = LAB_RESULT_ID
    lr.medical_case_id = CASE_ID
    lr.file = {"url": file_url}
    return lr



_PATCH_GET_LAB   = "app.tasks.pipeline._get_lab_result"
_PATCH_SET_OCR   = "app.tasks.pipeline._set_ocr_status"
_PATCH_SET_CASE  = "app.tasks.pipeline._set_case_status"
_PATCH_CREATE_I  = "app.tasks.pipeline._create_interpretation"
_PATCH_COMPLETE  = "app.tasks.pipeline._complete_interpretation"
_PATCH_FAIL_I    = "app.tasks.pipeline._fail_interpretation"
_PATCH_EXTRACT   = "app.services.ocr.extract_lab_values"
_PATCH_INTERPRET = "app.services.ai.generate_interpretation"
_PATCH_SESSION   = "app.db.session.AsyncSessionLocal"



async def test_happy_path_completes_both_stages():
    """
    A valid upload runs OCR and interpretation successfully.
    Both statuses end at COMPLETE; MedicalCase ends at COMPLETE.
    """
    from app.tasks.pipeline import _run_pipeline

    mock_session = AsyncMock()
    mock_session.__aenter__ = AsyncMock(return_value=mock_session)
    mock_session.__aexit__ = AsyncMock(return_value=False)

    with (
        patch(_PATCH_SESSION, return_value=mock_session),
        patch(_PATCH_GET_LAB, new_callable=AsyncMock, return_value=_make_lab_result()),
        patch(_PATCH_SET_OCR, new_callable=AsyncMock) as mock_set_ocr,
        patch(_PATCH_SET_CASE, new_callable=AsyncMock) as mock_set_case,
        patch(_PATCH_CREATE_I, new_callable=AsyncMock, return_value=INTERP_ID),
        patch(_PATCH_COMPLETE, new_callable=AsyncMock) as mock_complete,
        patch(_PATCH_EXTRACT, new_callable=AsyncMock, return_value=EXTRACTED),
        patch(_PATCH_INTERPRET, new_callable=AsyncMock, return_value=INTERPRETATION),
    ):
        await _run_pipeline(LAB_RESULT_ID)

    # OCR: PROCESSING then COMPLETE with extracted values
    ocr_calls = mock_set_ocr.call_args_list
    assert ocr_calls[0].args[2] == OCRStatus.PROCESSING
    assert ocr_calls[1].args[2] == OCRStatus.COMPLETE
    assert ocr_calls[1].kwargs["extracted_values"] == EXTRACTED

    # Case ends at COMPLETE
    mock_set_case.assert_called_once_with(mock_session, CASE_ID, MedicalCaseStatus.COMPLETE)

    # Interpretation completed with the AI result
    mock_complete.assert_called_once_with(mock_session, INTERP_ID, INTERPRETATION)


async def test_ocr_failure_marks_case_failed_no_interpretation():
    """
    When OCR raises OCRExtractionError, the case is marked FAILED
    and no AIInterpretation record is created.
    """
    from app.tasks.pipeline import _run_pipeline

    mock_session = AsyncMock()
    mock_session.__aenter__ = AsyncMock(return_value=mock_session)
    mock_session.__aexit__ = AsyncMock(return_value=False)

    with (
        patch(_PATCH_SESSION, return_value=mock_session),
        patch(_PATCH_GET_LAB, new_callable=AsyncMock, return_value=_make_lab_result()),
        patch(_PATCH_SET_OCR, new_callable=AsyncMock) as mock_set_ocr,
        patch(_PATCH_SET_CASE, new_callable=AsyncMock) as mock_set_case,
        patch(_PATCH_CREATE_I, new_callable=AsyncMock) as mock_create_i,
        patch(_PATCH_EXTRACT, new_callable=AsyncMock, side_effect=OCRExtractionError("blurry scan")),
    ):
        await _run_pipeline(LAB_RESULT_ID)

    # OCR status ends at FAILED
    last_ocr_call = mock_set_ocr.call_args_list[-1]
    assert last_ocr_call.args[2] == OCRStatus.FAILED

    # Case ends at FAILED
    mock_set_case.assert_called_once_with(mock_session, CASE_ID, MedicalCaseStatus.FAILED)

    # Interpretation was never created
    mock_create_i.assert_not_called()


async def test_interpretation_failure_marks_case_failed():
    """
    When OCR succeeds but interpretation raises InterpretationError,
    the interpretation is marked FAILED and the case is marked FAILED.
    """
    from app.tasks.pipeline import _run_pipeline

    mock_session = AsyncMock()
    mock_session.__aenter__ = AsyncMock(return_value=mock_session)
    mock_session.__aexit__ = AsyncMock(return_value=False)

    with (
        patch(_PATCH_SESSION, return_value=mock_session),
        patch(_PATCH_GET_LAB, new_callable=AsyncMock, return_value=_make_lab_result()),
        patch(_PATCH_SET_OCR, new_callable=AsyncMock),
        patch(_PATCH_SET_CASE, new_callable=AsyncMock) as mock_set_case,
        patch(_PATCH_CREATE_I, new_callable=AsyncMock, return_value=INTERP_ID),
        patch(_PATCH_FAIL_I, new_callable=AsyncMock) as mock_fail_i,
        patch(_PATCH_EXTRACT, new_callable=AsyncMock, return_value=EXTRACTED),
        patch(_PATCH_INTERPRET, new_callable=AsyncMock, side_effect=InterpretationError("model timeout")),
    ):
        await _run_pipeline(LAB_RESULT_ID)

    mock_fail_i.assert_called_once_with(mock_session, INTERP_ID)
    mock_set_case.assert_called_once_with(mock_session, CASE_ID, MedicalCaseStatus.FAILED)


async def test_missing_lab_result_aborts_gracefully():
    """
    If the LabResult row doesn't exist (race condition / bad ID),
    the pipeline aborts without touching any other record.
    """
    from app.tasks.pipeline import _run_pipeline

    mock_session = AsyncMock()
    mock_session.__aenter__ = AsyncMock(return_value=mock_session)
    mock_session.__aexit__ = AsyncMock(return_value=False)

    with (
        patch(_PATCH_SESSION, return_value=mock_session),
        patch(_PATCH_GET_LAB, new_callable=AsyncMock, return_value=None),
        patch(_PATCH_SET_OCR, new_callable=AsyncMock) as mock_set_ocr,
        patch(_PATCH_SET_CASE, new_callable=AsyncMock) as mock_set_case,
    ):
        await _run_pipeline(LAB_RESULT_ID)  # must not raise

    mock_set_ocr.assert_not_called()
    mock_set_case.assert_not_called()


async def test_missing_file_url_marks_failed():
    """
    A LabResult with an empty file URL (storage failure during upload)
    marks OCR and the case as FAILED without calling the OpenAI API.
    """
    from app.tasks.pipeline import _run_pipeline

    mock_session = AsyncMock()
    mock_session.__aenter__ = AsyncMock(return_value=mock_session)
    mock_session.__aexit__ = AsyncMock(return_value=False)

    with (
        patch(_PATCH_SESSION, return_value=mock_session),
        patch(_PATCH_GET_LAB, new_callable=AsyncMock, return_value=_make_lab_result(file_url="")),
        patch(_PATCH_SET_OCR, new_callable=AsyncMock) as mock_set_ocr,
        patch(_PATCH_SET_CASE, new_callable=AsyncMock) as mock_set_case,
        patch(_PATCH_EXTRACT, new_callable=AsyncMock) as mock_extract,
    ):
        await _run_pipeline(LAB_RESULT_ID)

    mock_set_ocr.assert_called_once_with(mock_session, LAB_RESULT_ID, OCRStatus.FAILED)
    mock_set_case.assert_called_once_with(mock_session, CASE_ID, MedicalCaseStatus.FAILED)
    mock_extract.assert_not_called()
