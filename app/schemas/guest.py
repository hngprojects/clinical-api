from datetime import datetime

from pydantic import BaseModel, ConfigDict


class GuestSessionResponse(BaseModel):
	"""Guest session issued to anonymous clients."""

	guest_session_id: str
	expires_in: int
	expires_at: datetime

	model_config = ConfigDict(from_attributes=True)
