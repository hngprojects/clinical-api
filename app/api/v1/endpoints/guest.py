from typing import Annotated

from fastapi import APIRouter, Depends, status

from app.api.deps import DBSession
from app.core.responses import SuccessResponse
from app.repositories.guest_session import GuestSessionRepository
from app.services.guest import create_guest_session

router = APIRouter(prefix="/guest", tags=["guest"])


def get_guest_repo(session: DBSession) -> GuestSessionRepository:
	return GuestSessionRepository(session)


GuestRepo = Annotated[GuestSessionRepository, Depends(get_guest_repo)]


@router.post(
	"/session",
	response_model=SuccessResponse,
	status_code=status.HTTP_201_CREATED,
)
async def create_session(
	guest_repo: GuestRepo,
) -> SuccessResponse:
	"""
	Create a new guest session.
	No authentication required.
	Returns a session_id the client must include as X-Guest-Session-ID
	header on subsequent requests.
	Session expires after 1 hour of inactivity.
	"""
	session = await create_guest_session(guest_repo)
	return SuccessResponse(
		message="Guest session created.",
		data={
			"session_id": session.session_id,
			"expires_at": session.expires_at.isoformat(),
		},
	)
