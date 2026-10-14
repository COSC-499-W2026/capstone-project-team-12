"""Deliver outbox mail over SMTP: Mailpit in development, the chosen provider in production."""

import smtplib
from email.message import EmailMessage

from app.config import Settings
from app.services.email_sender import MailQuotaError, OutgoingMail
from app.services.email_templates import render

# 421 service closing / too busy, 451 and 452 local error or out of resources: providers send
# these when a sending limit is reached, so the run stops instead of failing every row
_LIMIT_CODES = {421, 451, 452}
_TIMEOUT_SECONDS = 20


class SmtpMailer:
    """A `Mailer` that opens one SMTP connection per mail; the job sends at most 50 a minute."""

    def __init__(self, settings: Settings) -> None:
        self._settings = settings

    def send(self, mail: OutgoingMail) -> None:
        rendered = render(mail, self._settings.app_base_url)
        message = EmailMessage()
        message["From"] = self._settings.mail_from
        message["To"] = mail.to_email
        message["Subject"] = rendered.subject
        message.set_content(rendered.body)
        settings = self._settings
        try:
            with smtplib.SMTP(
                settings.smtp_host, settings.smtp_port, timeout=_TIMEOUT_SECONDS
            ) as smtp:
                if settings.smtp_starttls:
                    smtp.starttls()
                if settings.smtp_username:
                    smtp.login(settings.smtp_username, settings.smtp_password)
                smtp.send_message(message)
        except smtplib.SMTPResponseException as error:
            if error.smtp_code in _LIMIT_CODES:
                raise MailQuotaError(str(error)) from error
            raise
