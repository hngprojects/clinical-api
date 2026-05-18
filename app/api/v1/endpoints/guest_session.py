from fastapi import APIRouter, status

from app.api.deps import ClientIpHash, DeviceFingerprint, GuestSessionManagerDep
from app.core.responses import SuccessResponse
from app.schemas.guest import GuestSessionResponse
from app.services.guest import session_info

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
	session = await manager.create(ip_hash, device_fingerprint)
	info = session_info(session)
	return SuccessResponse(
		message="Guest session ready.",
		data=GuestSessionResponse(
			guest_session_id=info.guest_session_id,
			expires_in=info.expires_in,
		),
	)
