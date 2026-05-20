from uuid import UUID

from fastapi import APIRouter, status

from app.api.deps import ClientIpHash, DeviceFingerprint, GuestSessionId, GuestSessionManagerDep
from app.core.exceptions import UnauthorizedError
from app.core.rate_limit import enforce_guest_session_create_limit
from app.core.responses import SuccessResponse
from app.schemas.guest import GuestSessionResponse
from app.services.guest import normalize_guest_session_id, session_info

router = APIRouter(tags=["guest-sessions"])


@router.post(
	"/guest-session",
	response_model=SuccessResponse[GuestSessionResponse],
	status_code=status.HTTP_201_CREATED,
)
async def create_guest_session_route(
	manager: GuestSessionManagerDep,
	ip_hash: ClientIpHash,
	device_fingerprint: DeviceFingerprint,
) -> SuccessResponse[GuestSessionResponse]:
	"""Issue or return an existing guest session for this IP + device fingerprint."""
	await enforce_guest_session_create_limit(ip_hash)
	session = await manager.create(ip_hash, device_fingerprint)
	info = session_info(session)
	return SuccessResponse(
		message="Guest session ready.",
		data=GuestSessionResponse(
			guest_session_id=info.guest_session_id,
			expires_in=info.expires_in,
		),
	)


@router.get(
	"/guest-session/me",
	response_model=SuccessResponse[GuestSessionResponse],
)
async def guest_session_me(
	guest_session_id: GuestSessionId,
	manager: GuestSessionManagerDep,
) -> SuccessResponse[GuestSessionResponse]:
	"""Validate the caller's guest session and return remaining TTL."""
	if not guest_session_id:
		raise UnauthorizedError("Missing X-Guest-Session-Id header.")

	normalized = normalize_guest_session_id(guest_session_id)
	if normalized is None:
		raise UnauthorizedError("Invalid guest session id.")

	session = await manager.get(UUID(normalized))
	if session is None:
		raise UnauthorizedError("Guest session expired or invalid.")

	touched = await manager.touch(UUID(normalized))
	if touched is None:
		raise UnauthorizedError("Guest session expired or invalid.")

	info = session_info(touched)

	return SuccessResponse(
		message="OK",
		data=GuestSessionResponse(
			guest_session_id=info.guest_session_id,
			expires_in=info.expires_in,
		),
	)
