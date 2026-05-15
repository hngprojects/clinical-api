"""Build LLM context from case data and chat history."""

from __future__ import annotations

from typing import Any

from app.models.chat import Chat, SenderType

_NO_INTERPRETATION = "Interpretation not yet available."


def format_lab_values(extracted_values: dict[str, Any]) -> str:
	tests = extracted_values.get("tests", [])
	if not tests:
		return "No laboratory values available."
	lines = []
	for test in tests:
		name = test.get("name", "Unknown")
		value = test.get("value", "?")
		unit = test.get("unit") or ""
		ref = test.get("reference_range") or "not provided"
		unit_part = f" {unit}" if unit else ""
		lines.append(f"- {name}: {value}{unit_part} (ref: {ref})")
	return "\n".join(lines)


def build_case_context(
	extracted_values: dict[str, Any],
	interpretation_summary: str | None,
) -> str:
	lab_block = format_lab_values(extracted_values)
	summary = interpretation_summary.strip() if interpretation_summary else _NO_INTERPRETATION
	return f"LAB RESULTS:\n{lab_block}\n\nINTERPRETATION SUMMARY:\n{summary}"


def chats_to_messages(chats: list[Chat]) -> list[dict[str, str]]:
	"""Map persisted chat rows to LLM message dicts."""
	messages: list[dict[str, str]] = []
	for chat in chats:
		text = (chat.content or {}).get("text", "").strip()
		if not text:
			continue
		if chat.sender_type == SenderType.PATIENT:
			messages.append({"role": "user", "content": text})
		elif chat.sender_type == SenderType.AI:
			messages.append({"role": "assistant", "content": text})
	return messages
