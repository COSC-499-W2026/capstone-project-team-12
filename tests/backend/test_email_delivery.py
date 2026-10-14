"""Email templates, the SMTP mailer and the send-emails command."""

import smtplib
from collections.abc import Callable
from contextlib import nullcontext
from email.message import EmailMessage
from typing import Any

import pytest
from sqlalchemy.orm import Session

import app.cli
from app.config import Settings
from app.models.accounts import User
from app.models.enums import EmailKind, EmailStatus
from app.services.email import EmailService
from app.services.email_sender import MailQuotaError, OutgoingMail
from app.services.email_templates import LINK_PATHS, render
from app.services.smtp_mailer import SmtpMailer

BASE = "https://hunt.example.ca/"


def mail(kind: EmailKind, token: str | None = "tok-en_1", **params: Any) -> OutgoingMail:
    return OutgoingMail("student@example.com", kind, params, token)


@pytest.mark.parametrize("kind", list(LINK_PATHS))
def test_link_emails_point_at_their_page_with_the_token(kind: EmailKind) -> None:
    email = render(mail(kind), BASE)
    assert f"https://hunt.example.ca{LINK_PATHS[kind]}?token=tok-en_1\n" in email.body
    assert "tok-en_1" not in email.subject


@pytest.mark.parametrize("kind", list(LINK_PATHS))
def test_link_emails_need_a_token(kind: EmailKind) -> None:
    with pytest.raises(ValueError, match="needs a token"):
        render(mail(kind, token=None), BASE)


def test_mail_without_a_link() -> None:
    notice = render(mail(EmailKind.EMAIL_CHANGED_NOTICE, token=None), BASE)
    assert "http" not in notice.body
    invite = render(mail(EmailKind.TA_INVITATION, token=None, class_name="JAPN 101"), BASE)
    assert "JAPN 101" in invite.subject and "https://hunt.example.ca/\n" in invite.body


class FakeSmtp:
    calls: list[str]
    sent: list[EmailMessage]
    error: Exception | None = None

    def __init__(self, host: str, port: int, timeout: float) -> None:
        self.calls.append(f"connect {host}:{port}")

    def __enter__(self) -> "FakeSmtp":
        return self

    def __exit__(self, *exc: object) -> None:
        self.calls.append("quit")

    def starttls(self) -> None:
        self.calls.append("starttls")

    def login(self, user: str, password: str) -> None:
        self.calls.append(f"login {user}")

    def send_message(self, message: EmailMessage) -> None:
        if self.error is not None:
            raise self.error
        self.sent.append(message)


@pytest.fixture
def smtp(monkeypatch: pytest.MonkeyPatch) -> type[FakeSmtp]:
    FakeSmtp.calls, FakeSmtp.sent, FakeSmtp.error = [], [], None
    monkeypatch.setattr(smtplib, "SMTP", FakeSmtp)
    return FakeSmtp


def test_smtp_mailer_sends_the_rendered_email(smtp: type[FakeSmtp]) -> None:
    settings = Settings(smtp_host="mailpit", smtp_port=1025, mail_from="Hunt <a@b.ca>")
    SmtpMailer(settings).send(mail(EmailKind.RESET_PASSWORD))
    assert smtp.calls == ["connect mailpit:1025", "quit"]  # Mailpit: no TLS, no login
    (message,) = smtp.sent
    assert (message["From"], message["To"]) == ("Hunt <a@b.ca>", "student@example.com")
    assert message["Subject"] == "Reset your password"
    assert "token=tok-en_1" in message.get_content()


def test_smtp_mailer_uses_starttls_and_login_when_set(smtp: type[FakeSmtp]) -> None:
    settings = Settings(smtp_starttls=True, smtp_username="apikey", smtp_password="secret")
    SmtpMailer(settings).send(mail(EmailKind.VERIFY_EMAIL))
    assert smtp.calls[1:3] == ["starttls", "login apikey"]


@pytest.mark.parametrize(
    ("error", "raised"),
    [
        (smtplib.SMTPDataError(421, b"daily limit reached"), MailQuotaError),
        (smtplib.SMTPDataError(452, b"too many messages"), MailQuotaError),
        (smtplib.SMTPRecipientsRefused({"x": (550, b"no such user")}), smtplib.SMTPException),
        (smtplib.SMTPDataError(554, b"rejected"), smtplib.SMTPDataError),
    ],
    ids=["421", "452", "bad recipient", "554"],
)
def test_only_limit_replies_stop_the_run(
    smtp: type[FakeSmtp], error: Exception, raised: type[Exception]
) -> None:
    smtp.error = error
    with pytest.raises(raised) as caught:
        SmtpMailer(Settings()).send(mail(EmailKind.VERIFY_EMAIL))
    assert isinstance(caught.value, MailQuotaError) == (raised is MailQuotaError)


def test_send_emails_command_delivers_the_outbox(
    db: Session,
    make_user: Callable[..., User],
    smtp: type[FakeSmtp],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    row = EmailService.queue_email(make_user(), EmailKind.VERIFY_EMAIL, db)
    db.commit()
    monkeypatch.setattr(app.cli, "SessionLocal", lambda: nullcontext(db))
    assert app.cli.main(["send-emails"]) == 0
    assert [m["To"] for m in smtp.sent] == [row.to_email]
    db.refresh(row)
    assert row.status == EmailStatus.SENT


def test_unknown_command_is_rejected() -> None:
    with pytest.raises(SystemExit):
        app.cli.main(["send-everything"])
