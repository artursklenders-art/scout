import logging
import smtplib
import ssl
import time
from email.message import EmailMessage

from config import Config

logger = logging.getLogger(__name__)


class EmailSendError(RuntimeError):
    """Raised when email sending fails."""


def send_plain_email(config: Config, subject: str, body: str) -> None:
    if not body.strip():
        raise EmailSendError("Refusing to send an empty email body.")

    msg = EmailMessage()
    msg["From"] = config.smtp_user
    msg["To"] = config.email_to
    msg["Subject"] = subject
    msg.set_content(body)

    max_attempts = 2

    for attempt in range(1, max_attempts + 1):
        try:
            context = ssl.create_default_context()
            with smtplib.SMTP(config.smtp_host, config.smtp_port, timeout=30) as server:
                server.ehlo()
                server.starttls(context=context)
                server.ehlo()
                server.login(config.smtp_user, config.smtp_app_password)
                server.send_message(msg)
            return

        except smtplib.SMTPAuthenticationError as exc:
            raise EmailSendError(
                "SMTP authentication error. Check SMTP_USER and SMTP_APP_PASSWORD (Gmail App Password)."
            ) from exc

        except (smtplib.SMTPException, OSError) as exc:
            is_retryable = _is_temporary_smtp_error(exc)
            if is_retryable and attempt < max_attempts:
                logger.warning(
                    "Temporary SMTP error (attempt %s/%s): %s. Retrying once.",
                    attempt,
                    max_attempts,
                    exc,
                )
                time.sleep(2)
                continue

            raise EmailSendError(f"SMTP send failed: {exc}") from exc

    raise EmailSendError("SMTP send failed after retries.")


def _is_temporary_smtp_error(exc: Exception) -> bool:
    if isinstance(exc, smtplib.SMTPResponseException):
        return 400 <= exc.smtp_code < 500

    return isinstance(exc, smtplib.SMTPServerDisconnected)
