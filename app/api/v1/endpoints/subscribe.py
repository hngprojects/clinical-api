import logging
import httpx
from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel, EmailStr, Field

from app.core.config import get_settings

logger = logging.getLogger(__name__)
settings = get_settings()

router = APIRouter(prefix="/subscribe", tags=["subscribe"])


class SubscribeRequest(BaseModel):
	first_name: str
	email: EmailStr
	group_id: str      # MailerLite group ID (numeric string)
	tags: list[str] = Field(default_factory=list)


@router.post("", status_code=status.HTTP_200_OK)
async def subscribe(data: SubscribeRequest):
	"""Proxy contact subscription requests to MailerLite server-side."""
	api_key = settings.MAILERLITE_API_KEY
	if not api_key:
		logger.error("MAILERLITE_API_KEY environment variable is not configured.")
		raise HTTPException(
			status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
			detail="Subscription service is temporarily unavailable"
		)

	# Reconstruct the payload as expected by MailerLite API
	payload = {
		"email": data.email,
		"fields": {
			"name": data.first_name
		},
		"groups": [data.group_id],
		"status": "active"
	}

	# Add tags if any are supplied in request
	if data.tags:
		payload["tags"] = data.tags

	async with httpx.AsyncClient() as client:
		try:
			response = await client.post(
				"https://connect.mailerlite.com/api/subscribers",
				json=payload,
				headers={
					"Authorization": f"Bearer {api_key}",
					"Content-Type": "application/json"
				},
				timeout=10.0
			)
			if response.status_code not in (200, 201):
				logger.error(
					"MailerLite subscription returned error status: %s, Response: %s",
					response.status_code,
					response.text
				)
				raise HTTPException(
					status_code=status.HTTP_502_BAD_GATEWAY,
					detail="Subscription failed at MailerLite"
				)
		except httpx.RequestError as exc:
			logger.error("HTTP request to MailerLite failed: %s", exc)
			raise HTTPException(
				status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
				detail="Subscription provider is unreachable"
			)

	return {"success": True}
