from fastapi import APIRouter, status

from app.api.deps import ContactRepo
from app.core.responses import SuccessResponse
from app.schemas.contact import ContactRequest
from app.services.contact import submit_contact_message

router = APIRouter(prefix="/contact", tags=["contact"])


@router.post("", status_code=status.HTTP_200_OK, response_model=SuccessResponse[None])
async def contact_us(payload: ContactRequest, contact_repo: ContactRepo) -> SuccessResponse[None]:
	await submit_contact_message(contact_repo, payload)
	return SuccessResponse(message="Your message has been received. We'll get back to you shortly.")
