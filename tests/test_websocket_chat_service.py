"""
Unit tests for app/services/websocket_chat.py

These tests cover pure logic only - token counting, history trimming,
message serialisation. No database or network calls needed.
All DB-dependent functions (save_user_message, save_ai_message) are
tested with mocked repositories.
"""

import uuid
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.models.chat import Chat, SenderType
from app.services.websocket_chat import (
    _estimate_tokens,
    _history_to_text,
    chat_to_wire,
    trim_history,
    save_user_message,
    save_ai_message,
    generate_ai_response,
)


# --- Helpers ---

def make_chat(text: str, sender: SenderType = SenderType.PATIENT) -> Chat:
    """Create a Chat object without touching the database."""
    return Chat(
        id=uuid.uuid4(),
        sender_type=sender,
        content={"text": text},
        sent_at=datetime.now(timezone.utc),
        user_id=uuid.uuid4() if sender == SenderType.PATIENT else None,
        medical_case_id=uuid.uuid4(),
    )


# --- chat_to_wire ---

class TestChatToWire:
    def test_patient_message_serialises_correctly(self):
        msg = make_chat("What does high WBC mean?", SenderType.PATIENT)
        result = chat_to_wire(msg)

        assert result["content"] == "What does high WBC mean?"
        assert result["sender_type"] == "patient"
        assert result["id"] == str(msg.id)
        assert "sent_at" in result

    def test_ai_message_serialises_correctly(self):
        msg = make_chat("High WBC can indicate infection.", SenderType.AI)
        result = chat_to_wire(msg)

        assert result["content"] == "High WBC can indicate infection."
        assert result["sender_type"] == "ai"

    def test_missing_text_key_returns_empty_string(self):
        # Edge case: content dict exists but has no "text" key
        msg = make_chat("", SenderType.PATIENT)
        msg.content = {}  # simulate malformed content
        result = chat_to_wire(msg)

        assert result["content"] == ""

    def test_sent_at_is_iso_format(self):
        msg = make_chat("hello")
        result = chat_to_wire(msg)
        # Should parse back without error
        from datetime import datetime
        datetime.fromisoformat(result["sent_at"])


# --- _estimate_tokens ---

class TestEstimateTokens:
    def test_empty_string_returns_one(self):
        # max(1, ...) means empty string never returns 0
        assert _estimate_tokens("") == 1

    def test_short_string(self):
        # "hello" = 5 chars, 5 // 4 = 1
        assert _estimate_tokens("hello") == 1

    def test_longer_string(self):
        # 100 chars = 25 tokens
        assert _estimate_tokens("a" * 100) == 25

    def test_token_estimate_is_positive(self):
        assert _estimate_tokens("any text at all") > 0


# --- trim_history ---

class TestTrimHistory:
    def test_short_history_not_trimmed(self):
        history = [make_chat("hello"), make_chat("hi", SenderType.AI)]
        system_prompt = "You are a helpful assistant."
        result = trim_history(history, system_prompt, "new message")

        # Short history easily fits in 3000 tokens - nothing trimmed
        assert len(result) == 2

    def test_empty_history_returns_empty(self):
        result = trim_history([], "system prompt", "new message")
        assert result == []

    def test_long_history_gets_trimmed(self):
        # Create 100 messages with long content to force trimming
        history = [
            make_chat("a" * 200, SenderType.PATIENT if i % 2 == 0 else SenderType.AI)
            for i in range(100)
        ]
        system_prompt = "You are a helpful assistant."
        result = trim_history(history, system_prompt, "new question")

        # Should be trimmed - fewer than 100 messages remain
        assert len(result) < 100
        # But never below 2 (our hard floor)
        assert len(result) >= 2

    def test_never_trims_below_two_messages(self):
        # Even if system prompt alone exceeds budget, keep last 2
        history = [make_chat("msg1"), make_chat("msg2", SenderType.AI)]
        # Huge system prompt that eats all the budget
        huge_system = "word " * 5000
        result = trim_history(history, huge_system, "new message")

        assert len(result) == 2

    def test_single_message_history_not_trimmed_below_one(self):
        history = [make_chat("only message")]
        result = trim_history(history, "system", "new message")
        assert len(result) == 1

    def test_trim_removes_oldest_first(self):
        # Create messages with identifiable content
        history = [make_chat(f"message {i}") for i in range(20)]
        # Force trim by using a large system prompt
        large_system = "word " * 2000
        result = trim_history(history, large_system, "new message")

        if len(result) < len(history):
            # The remaining messages should be the most recent ones
            assert result[-1].content["text"] == "message 19"


# --- save_user_message ---

class TestSaveUserMessage:
    async def test_saves_with_correct_fields(self):
        mock_repo = MagicMock()
        mock_repo.add = MagicMock()
        mock_repo.commit = AsyncMock()
        mock_repo.refresh = AsyncMock()

        case_id = uuid.uuid4()
        user_id = uuid.uuid4()

        result = await save_user_message(mock_repo, case_id, user_id, "What is WBC?")

        # Verify add was called with correct data
        mock_repo.add.assert_called_once()
        saved_msg = mock_repo.add.call_args[0][0]

        assert saved_msg.content == {"text": "What is WBC?"}
        assert saved_msg.sender_type == SenderType.PATIENT
        assert saved_msg.medical_case_id == case_id
        assert saved_msg.user_id == user_id

    async def test_commits_and_refreshes(self):
        mock_repo = MagicMock()
        mock_repo.add = MagicMock()
        mock_repo.commit = AsyncMock()
        mock_repo.refresh = AsyncMock()

        await save_user_message(mock_repo, uuid.uuid4(), uuid.uuid4(), "hello")

        mock_repo.commit.assert_awaited_once()
        mock_repo.refresh.assert_awaited_once()


# --- save_ai_message ---

class TestSaveAiMessage:
    async def test_saves_with_correct_fields(self):
        mock_repo = MagicMock()
        mock_repo.add = MagicMock()
        mock_repo.commit = AsyncMock()
        mock_repo.refresh = AsyncMock()

        case_id = uuid.uuid4()

        await save_ai_message(mock_repo, case_id, "High WBC means infection risk.")

        saved_msg = mock_repo.add.call_args[0][0]

        assert saved_msg.content == {"text": "High WBC means infection risk."}
        assert saved_msg.sender_type == SenderType.AI
        assert saved_msg.medical_case_id == case_id
        assert saved_msg.user_id is None  # AI messages have no user_id

    async def test_commits_and_refreshes(self):
        mock_repo = MagicMock()
        mock_repo.add = MagicMock()
        mock_repo.commit = AsyncMock()
        mock_repo.refresh = AsyncMock()

        await save_ai_message(mock_repo, uuid.uuid4(), "response text")

        mock_repo.commit.assert_awaited_once()
        mock_repo.refresh.assert_awaited_once()


# --- generate_ai_response ---

class TestGenerateAiResponse:
    async def test_streams_tokens_from_llm(self):
        history = [make_chat("What is WBC?")]

        async def fake_stream(system, user, **kwargs):
            for token in ["High ", "WBC ", "means ", "infection."]:
                yield token

        with patch("app.services.websocket_chat.llm.stream_text_complete", fake_stream):
            tokens = []
            async for token in generate_ai_response("system prompt", history, "tell me more"):
                tokens.append(token)

        assert tokens == ["High ", "WBC ", "means ", "infection."]
        assert "".join(tokens) == "High WBC means infection."

    async def test_empty_history_still_works(self):
        async def fake_stream(system, user, **kwargs):
            yield "response"

        with patch("app.services.websocket_chat.llm.stream_text_complete", fake_stream):
            tokens = []
            async for token in generate_ai_response("system", [], "hello"):
                tokens.append(token)

        assert tokens == ["response"]

    async def test_conversation_prompt_includes_history(self):
        """Verify the user prompt passed to LLM includes conversation history."""
        history = [
            make_chat("What is WBC?", SenderType.PATIENT),
            make_chat("WBC stands for white blood cells.", SenderType.AI),
        ]
        captured_prompts = {}

        async def fake_stream(system, user, **kwargs):
            captured_prompts["user"] = user
            yield "token"

        with patch("app.services.websocket_chat.llm.stream_text_complete", fake_stream):
            async for _ in generate_ai_response("system", history, "tell me more"):
                pass

        # The user prompt should contain the history
        assert "What is WBC?" in captured_prompts["user"]
        assert "WBC stands for white blood cells." in captured_prompts["user"]
        assert "tell me more" in captured_prompts["user"]
