import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

from app.models.chat import SenderType


class ChatBase(BaseModel):
	"""Base schema for chat messages, used for both request and response models."""

	sender_type: SenderType
	content: dict
	medical_case_id: uuid.UUID


class ChatCreate(ChatBase):
	"""Request schema for creating a new chat message."""

	user_id: uuid.UUID | None = None


class ChatAsk(BaseModel):
	"""Request schema for asking a follow-up question in a case chat."""

	text: str = Field(..., min_length=1)


class ChatResponse(ChatBase):
	"""Response schema for chat messages, includes all fields from the database model."""

	id: uuid.UUID
	user_id: uuid.UUID | None
	sent_at: datetime

	model_config = ConfigDict(from_attributes=True)


class ChatExchangeResponse(BaseModel):
	"""Response containing the user message and the AI reply."""

	user_message: ChatResponse
	ai_message: ChatResponse
