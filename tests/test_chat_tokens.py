from app.services.chat_tokens import MAX_CONTEXT_TOKENS, estimate_tokens, truncate_history


def test_estimate_tokens_empty():
	assert estimate_tokens("") == 0


def test_truncate_history_preserves_case_context_budget():
	case_context = "LAB RESULTS:\n- Haemoglobin: 11.2 g/dL"
	messages = [{"role": "user", "content": "x" * 4000} for _ in range(20)]
	truncated = truncate_history(case_context, messages, max_tokens=MAX_CONTEXT_TOKENS)
	assert len(truncated) < len(messages)
	assert truncated[-1] == messages[-1]


def test_truncate_history_drops_oldest_first():
	case_context = "x" * 200
	messages = [{"role": "user", "content": f"message-{i} " * 50} for i in range(10)]
	truncated = truncate_history(case_context, messages, max_tokens=200)
	assert truncated
	assert len(truncated) < len(messages)
	assert truncated[-1] == messages[-1]
