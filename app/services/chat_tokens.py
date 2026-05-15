"""Token budgeting helpers for chat context windows."""

from __future__ import annotations

MAX_CONTEXT_TOKENS = 3000


def estimate_tokens(text: str) -> int:
	"""Approximate token count (~4 characters per token)."""
	if not text:
		return 0
	return max(1, len(text) // 4)


def truncate_history(
	case_context: str,
	messages: list[dict[str, str]],
	*,
	max_tokens: int = MAX_CONTEXT_TOKENS,
) -> list[dict[str, str]]:
	"""
	Trim conversation history to fit within max_tokens.

	Case context is always preserved; oldest messages are dropped first.
	"""
	case_tokens = estimate_tokens(case_context)
	remaining = max_tokens - case_tokens
	if remaining <= 0 or not messages:
		return []

	kept: list[dict[str, str]] = []
	for message in reversed(messages):
		content = message.get("content", "")
		msg_tokens = estimate_tokens(content) + 4  # role overhead
		if kept and sum(estimate_tokens(m.get("content", "")) + 4 for m in kept) + msg_tokens > remaining:
			break
		kept.append(message)

	kept.reverse()
	return kept
