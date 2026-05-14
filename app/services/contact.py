import logging

from app.models.contact import ContactMessage
from app.repositories.contact import ContactRepository
from app.schemas.contact import ContactRequest
from app.tasks.email import send_contact_feedback_email_task

logger = logging.getLogger(__name__)


async def submit_contact_message(
	contact_repo: ContactRepository,
	payload: ContactRequest,
) -> ContactMessage:
	"""Persist a contact-form submission and send the acknowledgement email."""
	record = ContactMessage(
		full_name=payload.full_name,
		email=str(payload.email),
		message=payload.message,
	)
	contact_repo.add(record)
	await contact_repo.commit()
	await contact_repo.refresh(record)

	try:
		send_contact_feedback_email_task.delay(
			full_name=payload.full_name,
			to_email=str(payload.email),
			message=payload.message,
		)
	except Exception as e:
		logging.error("Contact email failed with error => ", e)
		pass

	return record
