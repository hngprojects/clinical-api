from pydantic import BaseModel, ConfigDict


class GuestSessionResponse(BaseModel):
	"""Guest session issued to anonymous clients."""

	guest_session_id: str
	expires_in: int

	model_config = ConfigDict(from_attributes=True)
