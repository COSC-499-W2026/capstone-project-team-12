"""Deliver outbox mail over SMTP: Mailpit in development, the chosen provider in production."""

import smtplib
from email.message import EmailMessage
from email.utils import formatdate, make_msgid, parseaddr

from app.config import Settings
from app.services.email_sender import MailQuotaError, MailUnavailableError, OutgoingMail
from app.services.email_templates import render

# 421 service closing / too busy, 451 and 452 local error or out of resources
_LIMIT_CODES = {421, 451, 452}
# some providers answer a daily limit with a 5xx instead, e.g. Gmail's "550 5.4.5 Daily user
# sending limit exceeded"; check this list against the provider we end up choosing
_LIMIT_WORDS = ("5.4.5", "quota", "sending limit", "daily limit", "rate limit")
_TIMEOUT_SECONDS = 20


def _is_limit(code: int, reply: bytes | str) -> bool:
    text = (reply.decode(errors="replace") if isinstance(reply, bytes) else reply).lower()
    return code in _LIMIT_CODES or any(word in text for word in _LIMIT_WORDS)


class SmtpMailer:
    """A `Mailer` that opens one SMTP connection per mail; the job sends at most 50 a minute.

    A sending limit raises MailQuotaError, and a server that is down, unreachable or refuses our
    login raises MailUnavailableError, so the job pauses instead of failing every row. Any other
    error is about this one mail.
    """

    def __init__(self, settings: Settings) -> None:
        self._settings = settings

    def send(self, mail: OutgoingMail) -> None:
        message = self._message(mail)
        try:
            self._deliver(message)
        except smtplib.SMTPRecipientsRefused as error:
            if any(_is_limit(code, reply) for code, reply in error.recipients.values()):
                raise MailQuotaError(str(error)) from error
            raise  # this address is refused
        except smtplib.SMTPResponseException as error:
            if _is_limit(error.smtp_code, error.smtp_error):
                raise MailQuotaError(str(error)) from error
            if isinstance(
                error,
                smtplib.SMTPAuthenticationError | smtplib.SMTPConnectError | smtplib.SMTPHeloError,
            ):
                raise MailUnavailableError(str(error)) from error
            raise  # the server rejected this mail
        except OSError as error:
            # refused, timed out, DNS, disconnected, no STARTTLS (SMTPException is an OSError)
            raise MailUnavailableError(str(error) or type(error).__name__) from error

    def _message(self, mail: OutgoingMail) -> EmailMessage:
        rendered = render(mail, self._settings.app_base_url)
        message = EmailMessage()
        message["From"] = self._settings.mail_from
        message["To"] = mail.to_email
        message["Subject"] = rendered.subject
        message["Date"] = formatdate(usegmt=True)
        domain = parseaddr(self._settings.mail_from)[1].rpartition("@")[2] or None
        message["Message-ID"] = make_msgid(domain=domain)
        message.set_content(rendered.body)
        return message

    def _deliver(self, message: EmailMessage) -> None:
        settings = self._settings
        smtp = smtplib.SMTP(settings.smtp_host, settings.smtp_port, timeout=_TIMEOUT_SECONDS)
        try:
            if settings.smtp_starttls:
                smtp.starttls()
            if settings.smtp_username:
                smtp.login(settings.smtp_username, settings.smtp_password)
            smtp.send_message(message)
        except BaseException:
            smtp.close()
            raise
        # the mail is delivered now: a failed QUIT must not roll back its token or resend it
        try:
            smtp.quit()
        except (smtplib.SMTPException, OSError):
            smtp.close()
