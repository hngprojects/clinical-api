import logging

import resend

from app.core.config import get_settings
from app.core.exceptions import EmailError

logger = logging.getLogger(__name__)


def send_contact_feedback_email(full_name: str, to_email: str, message: str) -> None:
	"""Send a feedback acknowledgement email to the contact form submitter via Resend."""
	settings = get_settings()

	if not settings.RESEND_API_KEY:
		if settings.ALLOW_STDOUT_EMAIL:
			logger.info("STDOUT EMAIL [Contact Feedback] -> to: %s, name: %s", _mask_email(to_email), full_name)
			return
		raise EmailError(message="Email service not configured.")

	resend.api_key = settings.RESEND_API_KEY

	from_address = (
		f"{settings.RESEND_FROM_NAME} <{settings.RESEND_FROM_EMAIL}>"
		if settings.RESEND_FROM_NAME
		else settings.RESEND_FROM_EMAIL
	)

	subject = "We received your message — Clinsights"
	text_body = (
		f"Hi {full_name},\n\n"
		"Thank you for reaching out! We've received your message and will get back to you shortly.\n\n"
		f"Your message:\n{message}\n\n"
		"— The Clinsights Team"
	)
	html_body = f"""
<div style="font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif; max-width: 480px; margin: 0 auto;">
	<h2 style="color: #111;">Thanks for reaching out, {full_name}!</h2>
	<p style="color: #333; line-height: 1.5;">
		We've received your message and will get back to you shortly.
	</p>
	<div style="background: #f4f4f5; border-radius: 6px; padding: 16px; margin: 16px 0;">
		<p style="color: #555; font-size: 14px; margin: 0; white-space: pre-wrap;">{message}</p>
	</div>
	<p style="color: #666; font-size: 13px;">— The Clinsights Team</p>
</div>
""".strip()

	try:
		resend.Emails.send(
			{
				"from": from_address,
				"to": [to_email],
				"subject": subject,
				"text": text_body,
				"html": html_body,
			}
		)
	except Exception as e:
		raise EmailError(message=f"Failed to send contact feedback email: {e}") from e


def _mask_email(email: str) -> str:
	"""Redact all but the first two characters of the local part."""
	if "@" in email:
		local, domain = email.split("@", 1)
		return f"{local[:2]}***@{domain}"
	return "***"
