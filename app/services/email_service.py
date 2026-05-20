import logging
import threading
from enum import Enum
from pathlib import Path
from typing import Callable, NamedTuple, Tuple
from urllib.parse import urlparse

from jinja2 import Environment, FileSystemLoader, select_autoescape

from app.core.exceptions import EmailError
from app.core.responses import SuccessResponse
from app.services.mail_transport import send_with_fallback

logger = logging.getLogger(__name__)


class EMAIL_TYPE(str, Enum):
	PASSWORD_RESET = "PASSWORD_RESET"
	OTP = "OTP"
	WAITLIST = "WAITLIST"
	WELCOME = "WELCOME"
	CONTACT = "CONTACT"


_TEMPLATES_DIR = Path(__file__).parent.parent / "templates" / "emails"
_ENV = Environment(loader=FileSystemLoader(_TEMPLATES_DIR), autoescape=select_autoescape(["html", "xml"]))


def _validate_url(url: str | None) -> str:
	if not url:
		return ""
	try:
		parsed = urlparse(url)
		if parsed.scheme not in ("http", "https"):
			logger.warning("Invalid URL scheme: %s", parsed.scheme)
			return ""
		return url
	except Exception as exc:
		logger.warning("URL parsing failed: %s", exc)
		return ""


class _TemplateConfig(NamedTuple):
	template_doctor: str
	template_user: str
	subject: str
	prepare_context: Callable[[dict], None]


def _prepare_password_reset(ctx: dict) -> None:
	ctx["reset_url"] = _validate_url(ctx.get("reset_url", ""))
	ctx["expiration_time"] = ctx.get("expiration_time", "30")


def _prepare_otp(ctx: dict) -> None:
	pin = ctx.get("pin") or ctx.get("otp") or ctx.get("code", "")
	ctx["pin"] = pin
	ctx["otp"] = ctx.get("otp") or pin
	ctx["name"] = ctx.get("name") or ctx.get("first_name") or "there"
	ctx["baseUrl"] = ctx.get("baseUrl") or ctx.get("base_url") or ctx.get("frontend_url") or ""
	ctx["expirationTime"] = ctx.get("expiration_time", "10 minutes")


def _prepare_waitlist(ctx: dict) -> None:
	ctx["first_name"] = ctx.get("first_name", "there")


def _prepare_welcome(ctx: dict) -> None:
	ctx["cta_url"] = _validate_url(ctx.get("cta_url", ""))


def _prepare_contact(ctx: dict) -> None:
	ctx["full_name"] = ctx.get("full_name", "there")
	ctx["message"] = ctx.get("message", "")


_TEMPLATE_REGISTRY: dict[EMAIL_TYPE, _TemplateConfig] = {
	EMAIL_TYPE.PASSWORD_RESET: _TemplateConfig(
		template_doctor="password_reset_doctor.html",
		template_user="password_reset_user.html",
		subject="Reset Your Clinsight Password",
		prepare_context=_prepare_password_reset,
	),
	EMAIL_TYPE.OTP: _TemplateConfig(
		template_doctor="otp_doctor.html",
		template_user="otp_user.html",
		subject="Your Clinsight Verification Code",
		prepare_context=_prepare_otp,
	),
	EMAIL_TYPE.WAITLIST: _TemplateConfig(
		template_doctor="waitlist.html",
		template_user="waitlist.html",
		subject="You are on the Clinsight waitlist",
		prepare_context=_prepare_waitlist,
	),
	EMAIL_TYPE.WELCOME: _TemplateConfig(
		template_doctor="welcome_doctor.html",
		template_user="welcome_user.html",
		subject="Welcome to Clinsight",
		prepare_context=_prepare_welcome,
	),
	EMAIL_TYPE.CONTACT: _TemplateConfig(
		template_doctor="contact.html",
		template_user="contact.html",
		subject="We received your message — Clinsight",
		prepare_context=_prepare_contact,
	),
}


def _get_email_template_and_subject(email_type: EMAIL_TYPE, context: dict) -> Tuple[str, str]:
	config = _TEMPLATE_REGISTRY.get(email_type)
	if not config:
		raise EmailError("Unknown email type")

	config.prepare_context(context)
	is_doctor = context.get("is_doctor", False)
	template_name = config.template_doctor if is_doctor else config.template_user
	template = _ENV.get_template(template_name)
	html = template.render(context)
	return html, config.subject


async def send_email(email_type: EMAIL_TYPE, to: str, context: dict) -> SuccessResponse:
	try:
		html, subject = _get_email_template_and_subject(email_type, context)
	except EmailError:
		raise
	except Exception as exc:
		logger.exception("Template rendering failed")
		raise EmailError("Failed to render email template") from exc

	try:
		result = await send_with_fallback(subject=subject, html=html, to=to)
		logger.info("Email sent to %s via %s", to, result.get("provider"))
		return SuccessResponse(message="Email queued", data={"provider": result.get("provider")})
	except EmailError:
		logger.exception("Failed to send email to %s", to)
		raise


def send_email_sync(email_type: EMAIL_TYPE, to: str, context: dict) -> SuccessResponse:
	import asyncio as _asyncio

	def _run_send() -> dict:
		return _asyncio.run(send_email(email_type, to, context))

	try:
		_asyncio.get_running_loop()
	except RuntimeError:
		try:
			return _run_send()
		except Exception as exc:
			raise EmailError("Failed to send email (sync)") from exc

	result: dict[str, object] = {}
	error: list[BaseException] = []

	def _worker() -> None:
		try:
			result["value"] = _run_send()
		except BaseException as exc:  # pragma: no cover - defensive thread boundary
			error.append(exc)

	thread = threading.Thread(target=_worker, daemon=True)
	thread.start()
	thread.join()

	if error:
		raise EmailError("Failed to send email (sync)") from error[0]

	return result["value"]  # type: ignore[return-value]
