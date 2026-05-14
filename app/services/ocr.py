"""
OCR extraction service.

Sends the uploaded lab result file (image or PDF) to OpenAI's vision API
and returns a structured list of test values extracted from the document.

Design notes:
- The file is fetched from its stored URL and base64-encoded before being
  sent.
- The model is prompted to respond only in JSON so we can parse it
  deterministically.  Any non-JSON response is treated as an OCR failure.
"""

from __future__ import annotations

import base64
import json
import logging
from typing import Any

import httpx

from app.services.llm import vision_complete

logger = logging.getLogger(__name__)

# Prompt

_SYSTEM_PROMPT = """\
You are a medical document parser. Extract every laboratory test result from
the provided image or PDF page.

Return ONLY a JSON object — no markdown fences, no preamble — in this exact
shape:
{
  "tests": [
    {
      "name": "<test name>",
      "value": "<measured value as a string>",
      "unit": "<unit of measurement or null>",
      "reference_range": "<reference range string or null>"
    }
  ]
}

Rules:
- Include every test line visible in the document.
- Preserve the original value string exactly (e.g. "11.2", ">0.5", "NEGATIVE").
- If a field is absent from the document, use null.
- Do not add commentary, explanations, or any text outside the JSON object.
"""


def _detect_media_type(content: bytes, header: str) -> str:
	"""Infer media type from magic bytes, falling back to the Content-Type header."""
	if content[:4] == b"%PDF":
		return "application/pdf"
	if content[:3] == b"\xff\xd8\xff":
		return "image/jpeg"
	if content[:8] == b"\x89PNG\r\n\x1a\n":
		return "image/png"
	if content[:6] in (b"GIF87a", b"GIF89a"):
		return "image/gif"
	if content[:4] == b"RIFF" and content[8:12] == b"WEBP":
		return "image/webp"
	ct = header.split(";")[0].strip()
	if ct and ct not in ("application/octet-stream", "binary/octet-stream"):
		return ct
	return "image/jpeg"


_SUPPORTED_MEDIA_TYPES = {"image/jpeg", "image/png", "image/gif", "image/webp", "application/pdf"}


async def _fetch_file_as_base64(url: str) -> tuple[str, str]:
	"""Download *url* and return (base64_data, media_type).

	Raises OCRExtractionError if the URL does not point to a supported file type.
	"""
	async with httpx.AsyncClient(timeout=20) as client:
		response = await client.get(url)
		response.raise_for_status()

	media_type = _detect_media_type(
		response.content,
		response.headers.get("content-type", ""),
	)
	logger.info("[ocr] detected media_type=%s for %s", media_type, url)

	if media_type not in _SUPPORTED_MEDIA_TYPES:
		raise OCRExtractionError(
			f"Unsupported file type '{media_type}'. "
			"The file URL must point directly to an image (JPEG, PNG, GIF, WebP) or PDF, not a webpage."
		)

	data = base64.standard_b64encode(response.content).decode("utf-8")
	return data, media_type


# Public API


async def extract_lab_values(file_url: str) -> dict[str, Any]:
	"""
	Extract structured lab test values from the file at *file_url*.

	Returns a dict shaped as:
		{ "tests": [ { "name", "value", "unit", "reference_range" }, ... ] }

	Raises:
		OCRExtractionError — if the file cannot be fetched or parsed.
	"""
	try:
		data, media_type = await _fetch_file_as_base64(file_url)
	except Exception as exc:
		raise OCRExtractionError(f"Failed to fetch lab result file: {exc}") from exc

	try:
		raw_text = await vision_complete(
			_SYSTEM_PROMPT,
			"Extract all laboratory test results from this document.",
			data,
			media_type,
			max_tokens=1500,
		)
	except Exception as exc:
		raise OCRExtractionError(f"LLM call failed: {exc}") from exc

	try:
		extracted: dict[str, Any] = json.loads(raw_text)
	except json.JSONDecodeError as exc:
		logger.error("[ocr] non-JSON response from model: %s", raw_text[:200])
		raise OCRExtractionError("Model returned non-JSON response") from exc

	if "tests" not in extracted or not isinstance(extracted["tests"], list):
		raise OCRExtractionError("Model response missing 'tests' array")

	logger.info("[ocr] extracted %d tests from %s", len(extracted["tests"]), file_url)
	return extracted


class OCRExtractionError(Exception):
	"""Raised when OCR extraction cannot produce a usable result."""
