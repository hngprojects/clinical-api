"""
AI interpretation service.

Takes the structured lab values extracted by the OCR stage and produces a
plain-language clinical interpretation using GPT-4o-mini.

Design notes:
- The model is asked to classify each value as "normal", "caution", or
  "abnormal" using the reference range already present in the extracted data.
  We do not maintain a separate reference database — the ranges printed on
  the patient's own report are the authoritative source for this MVP.
- A mandatory medical disclaimer is injected into the system prompt, not the
  summary, so it cannot be overwritten by prompt injection attempts.
- Confidence is self-reported by the model (low/medium/high) based on how
  complete and legible the extracted data was.
- Risk level is derived from the worst per-value status: any "abnormal" → high,
  any "caution" → moderate, otherwise → low.
"""

from __future__ import annotations

import json
import logging
from typing import Any

from app.services.llm import text_complete

logger = logging.getLogger(__name__)

# ── Prompts ─────────────────────────────────────────────────────────────

_SYSTEM_PROMPT = """\
You are a clinical decision-support assistant. Your role is to explain
laboratory results in plain, empathetic language that a non-medical person
can understand.

IMPORTANT: Always append this disclaimer verbatim to your summary field:
"This is not a medical diagnosis. Please consult a qualified healthcare
professional for personalised advice."

Respond ONLY with a JSON object — no markdown fences, no preamble — shaped
exactly as:
{
  "summary": "<2–4 sentence plain-language overview including the disclaimer>",
  "value_breakdown": [
    {
      "metric": "<test name>",
      "value": "<measured value>",
      "unit": "<unit or null>",
      "status": "normal" | "caution" | "abnormal"
    }
  ],
  "suggested_questions": ["<question 1>", "<question 2>", "<question 3>"],
  "risk_level": "low" | "moderate" | "high",
  "confidence": "low" | "medium" | "high"
}

Classification rules:
- "normal"   — value falls within the printed reference range
- "caution"  — value is borderline or reference range is absent
- "abnormal" — value is clearly outside the printed reference range
- risk_level — "high" if any value is abnormal, "moderate" if any is caution,
               "low" if all are normal
- confidence — "high" if all tests have values and reference ranges,
               "medium" if some are missing, "low" if most are missing
"""


# Public API


async def generate_interpretation(extracted_values: dict[str, Any]) -> dict[str, Any]:
	"""
	Generate a plain-language interpretation for the extracted lab values.

	Args:
		extracted_values: Output of ocr.extract_lab_values — shaped as
			{ "tests": [ { name, value, unit, reference_range } ] }

	Raises:
		InterpretationError — if the model returns an unusable response.
	"""
	tests = extracted_values.get("tests", [])
	if not tests:
		raise InterpretationError("No test values to interpret")

	rows = "\n".join(
		f"- {t.get('name', 'Unknown')}: {t.get('value', '?')} {t.get('unit') or ''} "
		f"(ref: {t.get('reference_range') or 'not provided'})"
		for t in tests
	)
	user_message = f"Please interpret these laboratory results:\n\n{rows}"

	try:
		raw_text = await text_complete(
			_SYSTEM_PROMPT,
			user_message,
			max_tokens=1200,
			temperature=0.2,
		)
	except Exception as exc:
		raise InterpretationError(f"LLM call failed: {exc}") from exc

	try:
		result: dict[str, Any] = json.loads(raw_text)
	except json.JSONDecodeError as exc:
		logger.error("[ai] non-JSON response from model: %s", raw_text[:200])
		raise InterpretationError("Model returned non-JSON response") from exc

	required = {"summary", "value_breakdown", "suggested_questions", "risk_level", "confidence"}
	missing = required - result.keys()
	if missing:
		raise InterpretationError(f"Model response missing fields: {missing}")

	logger.info(
		"[ai] interpretation complete — risk_level=%s confidence=%s",
		result.get("risk_level"),
		result.get("confidence"),
	)
	return result


class InterpretationError(Exception):
	"""Raised when AI interpretation cannot produce a usable result."""


MEDICAL_DISCLAIMER = (
	"This is not a medical diagnosis. Please consult a qualified healthcare professional for personalised advice."
)

_CHAT_SYSTEM_PROMPT = f"""\
You are a clinical decision-support assistant helping a patient understand
their laboratory results. Answer follow-up questions using ONLY the lab values
and interpretation summary provided in the case context.

Rules:
- Reference specific test names and values from the case context when relevant.
- If asked about topics unrelated to this report, explain you can only discuss
  these laboratory results.
- Do not provide a diagnosis or prescribe treatment.
- Always end your reply with this disclaimer verbatim:
"{MEDICAL_DISCLAIMER}"

Respond in plain, empathetic language. Return only the reply text — no JSON,
no markdown fences.
"""


async def generate_chat_response(
	*,
	case_context: str,
	messages: list[dict[str, str]],
) -> str:
	"""
	Generate a follow-up chat reply grounded in case lab data and history.

	Args:
		case_context: Formatted lab values and interpretation summary.
		messages: Prior turns as {role, content} with role user|assistant.

	Raises:
		ChatError — if the model returns an empty or unusable response.
	"""
	if not case_context.strip():
		raise ChatError("Case context is required for chat")

	history_lines: list[str] = []
	for message in messages:
		role = message.get("role", "user")
		label = "Patient" if role == "user" else "Assistant"
		history_lines.append(f"{label}: {message.get('content', '')}")

	history_block = "\n".join(history_lines) if history_lines else "(no prior messages)"
	user_message = f"=== CASE CONTEXT ===\n{case_context}\n\n=== CONVERSATION ===\n{history_block}"

	try:
		raw_text = await text_complete(
			_CHAT_SYSTEM_PROMPT,
			user_message,
			max_tokens=800,
			temperature=0.3,
		)
	except Exception as exc:
		raise ChatError(f"LLM call failed: {exc}") from exc

	reply = raw_text.strip()
	if not reply:
		raise ChatError("Model returned an empty response")

	if MEDICAL_DISCLAIMER not in reply:
		reply = f"{reply}\n\n{MEDICAL_DISCLAIMER}"

	logger.info("[ai] chat response generated (%d chars)", len(reply))
	return reply


class ChatError(Exception):
	"""Raised when AI chat cannot produce a usable result."""
