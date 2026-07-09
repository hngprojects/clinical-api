import logging

from fastapi import APIRouter, status

from app.api.deps import ClientIpHash, WaitlistRepo
from app.core.config import get_settings
from app.core.rate_limit import enforce_rate_limit
from app.core.responses import SuccessResponse
from app.schemas.waitlist import WaitlistCreate, WaitlistResponse
from app.services.waitlist import join_waitlist
from app.tasks.emails import send_waitlist_email_task

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/waitlist", tags=["waitlist"])


@router.post(
	"",
	response_model=SuccessResponse[WaitlistResponse],
	status_code=status.HTTP_201_CREATED,
)
async def join(
	payload: WaitlistCreate,
	waitlist_repo: WaitlistRepo,
	ip_hash: ClientIpHash,
) -> SuccessResponse[WaitlistResponse]:
	"""Add an email to the waitlist."""
	settings = get_settings()
	await enforce_rate_limit(
		key=f"rl:waitlist:{ip_hash}",
		limit=settings.WAITLIST_RATE_LIMIT,
		window_seconds=settings.WAITLIST_RATE_WINDOW_SECONDS,
	)
	entry = await join_waitlist(waitlist_repo, payload)
	try:
		send_waitlist_email_task.delay(to_email=entry.email, first_name=payload.first_name)
	except Exception:
		logger.exception("Failed to enqueue waitlist email for %s", entry.email)
	return SuccessResponse(
		message="You've been added to the waitlist!",
		data=WaitlistResponse.model_validate(entry),
	)
