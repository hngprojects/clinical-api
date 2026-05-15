import logging

from celery import shared_task

from app.services.email_service import EMAIL_TYPE, send_email_sync

logger = logging.getLogger(__name__)


@shared_task(bind=True, max_retries=3, default_retry_delay=30)
def send_password_reset_email_task(self, to_email: str, reset_token: str, reset_url: str | None = None) -> None:
    try:
        send_email_sync(EMAIL_TYPE.PASSWORD_RESET, to_email, {"reset_token": reset_token, "reset_url": reset_url})
    except Exception as exc:
        logger.warning("Password reset email task failed (retrying): %s", exc, exc_info=True)
        raise self.retry(exc=exc) from exc


@shared_task(bind=True, max_retries=3, default_retry_delay=30)
def send_otp_email_task(
    self,
    to_email: str,
    code: str,
    purpose: str = "OTP",
    first_name: str | None = None,
) -> None:
    try:
        send_email_sync(
            EMAIL_TYPE.OTP,
            to_email,
            {"code": code, "purpose": purpose, "first_name": first_name},
        )
    except Exception as exc:
        logger.warning("OTP email task failed (retrying): %s", exc, exc_info=True)
        raise self.retry(exc=exc) from exc


@shared_task(bind=True, max_retries=3, default_retry_delay=30)
def send_waitlist_email_task(self, to_email: str, name: str | None = None) -> None:
    try:
        send_email_sync(EMAIL_TYPE.WAITLIST, to_email, {"name": name})
    except Exception as exc:
        logger.warning("Waitlist email task failed (retrying): %s", exc, exc_info=True)
        raise self.retry(exc=exc) from exc


@shared_task(bind=True, max_retries=3, default_retry_delay=30)
def send_welcome_email_task(self, to_email: str, first_name: str | None = None) -> None:
    try:
        send_email_sync(EMAIL_TYPE.WELCOME, to_email, {"first_name": first_name})
    except Exception as exc:
        logger.warning("Welcome email task failed (retrying): %s", exc, exc_info=True)
        raise self.retry(exc=exc) from exc


@shared_task(bind=True, max_retries=3, default_retry_delay=30)
def send_contact_feedback_email_task(self, to_email: str, full_name: str, message: str) -> None:
    try:
        send_email_sync(EMAIL_TYPE.CONTACT, to_email, {"full_name": full_name, "message": message})
    except Exception as exc:
        logger.warning("Contact feedback email task failed (retrying): %s", exc, exc_info=True)
        raise self.retry(exc=exc) from exc
