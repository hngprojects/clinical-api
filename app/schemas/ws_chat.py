from uuid import UUID

from pydantic import BaseModel, field_validator

# ── Client → Server
# These are messages the client sends to us. We validate them with Pydantic
# so malformed messages are caught immediately with a clear error.


class InitMessage(BaseModel):
	"""First message the client sends after the WebSocket connection opens.
	Must arrive within 10 seconds or we close the connection."""

	type: str
	case_id: UUID
	token: str  # the user's JWT access token — auth happens here, not in headers

	@field_validator("type")
	@classmethod
	def must_be_init(cls, v: str) -> str:
		if v != "init":
			raise ValueError("type must be 'init'")
		return v


class UserMessage(BaseModel):
	"""A chat message the patient sends during an active conversation."""

	type: str
	content: str

	@field_validator("type")
	@classmethod
	def must_be_message(cls, v: str) -> str:
		if v != "message":
			raise ValueError("type must be 'message'")
		return v

	@field_validator("content")
	@classmethod
	def content_not_empty(cls, v: str) -> str:
		v = v.strip()
		if not v:
			raise ValueError("content must not be empty")
		return v


# ── Server → Client
# These are plain functions that return dicts — not Pydantic models.
# Reason: these are sent at high frequency (one per token during streaming).
# Building and serialising a Pydantic model for every token would be unnecessary overhead.
# Plain dicts serialise directly via send_json().


def make_history(messages: list[dict]) -> dict:
	"""Sent once on connect — the last 50 messages so the client can show history."""
	return {"type": "history", "messages": messages}


def make_token(content: str) -> dict:
	"""Sent once per token during AI streaming — client appends each to the response bubble."""
	return {"type": "token", "content": content}


def make_done(message_id: str) -> dict:
	"""Sent when AI finishes responding — includes the saved message ID for the client to reference."""
	return {"type": "done", "message_id": message_id}


def make_queued(position: int) -> dict:
	"""
	Sent when a user message arrives while AI is still responding to the previous one.
	Tells the client their message was received and will be processed in order.
	"""
	return {"type": "queued", "position": position}


def make_error(code: str, detail: str) -> dict:
	"""Sent when something goes wrong. Connection stays open unless we explicitly close it.
	Codes we use: UNAUTHORIZED, FORBIDDEN, VALIDATION_ERROR, AI_ERROR, UNKNOWN_TYPE"""
	return {"type": "error", "code": code, "detail": detail}


def make_pong() -> dict:
	"""Response to a client ping — keeps the connection alive through proxies."""
	return {"type": "pong"}
