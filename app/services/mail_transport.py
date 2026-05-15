import asyncio
import logging
import smtplib
import ssl
from abc import ABC, abstractmethod
from email.message import EmailMessage
from typing import Any

import httpx

from app.core.config import get_settings
from app.core.exceptions import EmailError

logger = logging.getLogger(__name__)


class BaseMailTransport(ABC):
	@abstractmethod
	async def send(
		self, subject: str, html: str, to: str, from_email: str | None = None
	) -> Any:  # pragma: no cover - thin wrapper
		raise NotImplementedError()


class ResendTransport(BaseMailTransport):
	def __init__(self, api_key: str, from_email: str, from_name: str | None = None) -> None:
		self.api_key = api_key
		self.from_email = from_email
		self.from_name = from_name or ""
		self.base_url = "https://api.resend.com"

	async def send(self, subject: str, html: str, to: str, from_email: str | None = None) -> dict:
		payload = {
			"from": f"{self.from_name} <{from_email or self.from_email}>",
			"to": [to],
			"subject": subject,
			"html": html,
		}

		headers = {"Authorization": f"Bearer {self.api_key}", "Content-Type": "application/json"}

		async with httpx.AsyncClient(timeout=10) as client:
			try:
				resp = await client.post(f"{self.base_url}/emails", json=payload, headers=headers)
			except Exception as exc:  # network / transport
				logger.exception("Resend request failed for %s", to)
				raise EmailError("Resend provider request failed") from exc

		if resp.status_code >= 400:
			logger.warning("Resend returned status %s: %s", resp.status_code, resp.text)
			raise EmailError(f"Resend provider error: {resp.status_code} {resp.text}")

		return resp.json()


class SMTPTransport(BaseMailTransport):
	def __init__(
		self,
		host: str,
		port: int,
		username: str | None,
		password: str | None,
		use_tls: bool,
		from_email: str,
		from_name: str | None = None,
	) -> None:
		self.host = host
		self.port = port
		self.username = username
		self.password = password
		self.use_tls = use_tls
		self.from_email = from_email
		self.from_name = from_name or ""

	async def send(self, subject: str, html: str, to: str, from_email: str | None = None) -> dict:
		message = EmailMessage()
		message["Subject"] = subject
		message["From"] = f"{self.from_name} <{from_email or self.from_email}>"
		message["To"] = to
		message.set_content(html, subtype="html")

		def _smtp_send() -> dict:
			try:
				if self.use_tls:
					context = ssl.create_default_context()
					with smtplib.SMTP(self.host, self.port, timeout=10) as smtp:
						smtp.starttls(context=context)
						if self.username:
							smtp.login(self.username, self.password or "")
						smtp.send_message(message)
				else:
					with smtplib.SMTP(self.host, self.port, timeout=10) as smtp:
						if self.username:
							smtp.login(self.username, self.password or "")
						smtp.send_message(message)
			except Exception:  # pragma: no cover - network
				logger.exception("SMTP send failed to %s", to)
				raise
			return {"message": "sent"}

		try:
			return await asyncio.to_thread(_smtp_send)
		except Exception as exc:
			raise EmailError("SMTP provider failed") from exc


def _build_resend_transport() -> ResendTransport | None:
	settings = get_settings()
	key = settings.RESEND_API_KEY
	from_email = settings.RESEND_FROM_EMAIL
	from_name = settings.RESEND_FROM_NAME
	if key and from_email:
		return ResendTransport(api_key=key, from_email=from_email, from_name=from_name)
	return None


def _build_smtp_transport() -> SMTPTransport | None:
	settings = get_settings()
	host = settings.SMTP_HOST
	port = settings.SMTP_PORT
	if not host or not port:
		return None
	username = settings.SMTP_USERNAME
	password = settings.SMTP_PASSWORD
	use_tls = settings.SMTP_USE_TLS
	from_email = settings.SMTP_FROM_EMAIL or settings.RESEND_FROM_EMAIL
	from_name = settings.SMTP_FROM_NAME or settings.RESEND_FROM_NAME
	try:
		port_int = int(port)
	except Exception:
		port_int = 587
	return SMTPTransport(
		host=host,
		port=port_int,
		username=username,
		password=password,
		use_tls=use_tls,
		from_email=from_email or "",
		from_name=from_name,
	)


async def send_with_fallback(subject: str, html: str, to: str) -> dict:
	settings = get_settings()
	allow_stdout = settings.ALLOW_STDOUT_EMAIL

	if allow_stdout:
		logger.info("[EMAIL-STDOUT] To: %s Subject: %s", to, subject)
		return {"provider": "stdout"}

	resend = _build_resend_transport()
	smtp = _build_smtp_transport()

	if resend:
		try:
			return {"provider": "resend", "result": await resend.send(subject=subject, html=html, to=to)}
		except EmailError as exc:
			logger.warning("Resend failed, will attempt SMTP fallback: %s", exc)
			# fall through to smtp

	if smtp:
		try:
			return {"provider": "smtp", "result": await smtp.send(subject=subject, html=html, to=to)}
		except EmailError:
			logger.exception("SMTP fallback also failed")
			raise

	raise EmailError("No email providers configured")
