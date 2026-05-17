from fastapi import APIRouter, status

from app.api.deps import GuestSessionId
from app.core.exceptions import UnauthorizedError
from app.core.responses import SuccessResponse
from app.schemas.guest import GuestSessionResponse
from app.services.guest import create_guest_session, get_guest_session, touch_guest_session

router = APIRouter(prefix="/guest/sessions", tags=["guest-sessions"])


@router.post(
	"",
	response_model=SuccessResponse[GuestSessionResponse],
	status_code=status.HTTP_201_CREATED,
)
async def create_session() -> SuccessResponse[GuestSessionResponse]:
	"""Issue a new guest session id (stored in Redis with TTL)."""
	info = await create_guest_session()
	return SuccessResponse(
		message="Guest session created.",
		data=GuestSessionResponse(
			guest_session_id=info.guest_session_id,
			expires_in=info.expires_in,
		),
	)


@router.get(
	"/me",
	response_model=SuccessResponse[GuestSessionResponse],
)
async def session_me(
	guest_session_id: GuestSessionId,
) -> SuccessResponse[GuestSessionResponse]:
	"""Validate the caller's guest session and return remaining TTL."""
	if not guest_session_id:
		raise UnauthorizedError("Missing X-Guest-Session-Id header.")

	info = await get_guest_session(guest_session_id)
	if info is None:
		raise UnauthorizedError("Guest session expired or invalid.")

	await touch_guest_session(guest_session_id)
	refreshed = await get_guest_session(guest_session_id)
	if refreshed is None:
		raise UnauthorizedError("Guest session expired or invalid.")

	return SuccessResponse(
		message="OK",
		data=GuestSessionResponse(
			guest_session_id=refreshed.guest_session_id,
			expires_in=refreshed.expires_in,
		),
	)
