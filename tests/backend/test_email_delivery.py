"""Email templates, the SMTP mailer and the send-emails command."""

import re
import smtplib
import ssl
from collections.abc import Callable
from contextlib import nullcontext
from email.message import EmailMessage
from typing import Any

import pytest
from sqlalchemy import func, select
from sqlalchemy.orm import Session

import app.cli
from app.config import Settings
from app.models.accounts import User
from app.models.enums import EmailKind, EmailStatus
from app.services.email import EmailService
from app.services.email_sender import (
    SERVER_PAUSE,
    MailQuotaError,
    MailUnavailableError,
    OutgoingMail,
    SendReport,
    send_due_emails,
)
from app.services.email_templates import LINK_PATHS, render
from app.services.smtp_mailer import SmtpMailer

BASE = "https://hunt.example.ca/"


def settings(**fields: Any) -> Settings:
    return Settings(_env_file=None, **fields)  # not the developer's own .env


def mail(kind: EmailKind, token: str | None = "tok-en_1", **params: Any) -> OutgoingMail:
    return OutgoingMail("student@example.com", kind, params, token)


# expiry each email promises, matching TOKEN_TTL
LASTS = {
    EmailKind.VERIFY_EMAIL: "7 days",
    EmailKind.RESET_PASSWORD: "60 minutes",
    EmailKind.SET_PASSWORD: "3 days",
}


@pytest.mark.parametrize("kind", list(LINK_PATHS))
def test_link_emails_point_at_their_page_with_the_token(kind: EmailKind) -> None:
    email = render(mail(kind), BASE)
    assert f"https://hunt.example.ca{LINK_PATHS[kind]}?token=tok-en_1\n" in email.body
    assert "tok-en_1" not in email.subject
    assert LASTS[kind] in email.body


def test_link_emails_need_a_token_and_other_kinds_have_no_template_yet() -> None:
    with pytest.raises(ValueError, match="needs a token"):
        render(mail(EmailKind.RESET_PASSWORD, token=None), BASE)
    with pytest.raises(ValueError, match="No template"):
        render(mail(EmailKind.TA_INVITATION, token=None), BASE)


class FakeSmtp:
    calls: list[str]
    sent: list[EmailMessage]
    errors: dict[str, Exception]  # step ("connect", "starttls", "login", "send", "quit") -> error
    tls: ssl.SSLContext | None

    def __init__(self, host: str, port: int, timeout: float) -> None:
        self.step("connect", f"connect {host}:{port}")

    def step(self, name: str, call: str) -> None:
        self.calls.append(call)
        if name in self.errors:
            raise self.errors[name]

    def starttls(self, context: ssl.SSLContext | None = None) -> None:
        FakeSmtp.tls = context
        self.step("starttls", "starttls")

    def login(self, user: str, password: str) -> None:
        self.step("login", f"login {user}")

    def send_message(self, message: EmailMessage) -> None:
        self.step("send", "send")
        self.sent.append(message)

    def quit(self) -> None:
        self.step("quit", "quit")

    def close(self) -> None:
        self.calls.append("close")


@pytest.fixture
def smtp(monkeypatch: pytest.MonkeyPatch) -> type[FakeSmtp]:
    FakeSmtp.calls, FakeSmtp.sent, FakeSmtp.errors, FakeSmtp.tls = [], [], {}, None
    monkeypatch.setattr(smtplib, "SMTP", FakeSmtp)
    return FakeSmtp


def test_smtp_mailer_sends_the_rendered_email(smtp: type[FakeSmtp]) -> None:
    mailer = SmtpMailer(settings(smtp_host="mailpit", smtp_port=1025, mail_from="Hunt <a@b.ca>"))
    mailer.send(mail(EmailKind.RESET_PASSWORD))
    assert smtp.calls == ["connect mailpit:1025", "send", "quit"]  # Mailpit: no TLS, no login
    (message,) = smtp.sent
    assert (message["From"], message["To"]) == ("Hunt <a@b.ca>", "student@example.com")
    assert message["Subject"] == "Reset your password"
    assert message["Date"] and message["Message-ID"].endswith("@b.ca>")  # spam filters want both
    assert "token=tok-en_1" in message.get_content()


def test_smtp_mailer_uses_starttls_and_login_when_set(smtp: type[FakeSmtp]) -> None:
    login = settings(smtp_starttls=True, smtp_username="apikey", smtp_password="secret")
    SmtpMailer(login).send(mail(EmailKind.VERIFY_EMAIL))
    assert smtp.calls[1:3] == ["starttls", "login apikey"]
    assert smtp.tls is not None and smtp.tls.verify_mode == ssl.CERT_REQUIRED
    assert smtp.tls.check_hostname  # so no one in between can read the password or links


def test_a_failed_quit_after_sending_is_ignored(smtp: type[FakeSmtp]) -> None:
    smtp.errors["quit"] = smtplib.SMTPServerDisconnected("gone")
    SmtpMailer(settings()).send(mail(EmailKind.VERIFY_EMAIL))  # delivered, so no error
    assert len(smtp.sent) == 1 and smtp.calls[-2:] == ["quit", "close"]


def refused(code: int, reply: bytes) -> smtplib.SMTPRecipientsRefused:
    return smtplib.SMTPRecipientsRefused({"student@example.com": (code, reply)})


@pytest.mark.parametrize(
    ("step", "error", "raised"),
    [
        ("send", smtplib.SMTPDataError(452, b"too many messages"), MailQuotaError),
        ("send", smtplib.SMTPDataError(550, b"5.4.5 Daily sending limit exceeded"), MailQuotaError),
        ("send", refused(450, b"4.7.1 rate limit exceeded"), MailQuotaError),
        ("send", smtplib.SMTPDataError(421, b"try again later"), MailUnavailableError),
        ("send", smtplib.SMTPDataError(451, b"local error in processing"), MailUnavailableError),
        ("send", refused(421, b"4.7.0 try again later"), MailUnavailableError),
        ("send", smtplib.SMTPSenderRefused(553, b"not allowed", "a@b.ca"), MailUnavailableError),
        ("send", smtplib.SMTPServerDisconnected("connection lost"), MailUnavailableError),
        # anything before the mail itself goes out, whatever its code
        ("starttls", smtplib.SMTPResponseException(454, b"no TLS"), MailUnavailableError),
        ("login", smtplib.SMTPAuthenticationError(535, b"bad credentials"), MailUnavailableError),
        # about one recipient or this message, so only this mail fails
        ("send", refused(452, b"4.2.2 over quota"), smtplib.SMTPRecipientsRefused),
        ("send", refused(550, b"no such user"), smtplib.SMTPRecipientsRefused),
        ("send", smtplib.SMTPDataError(552, b"5.2.2 mailbox full"), smtplib.SMTPDataError),
        ("send", smtplib.SMTPDataError(554, b"message rejected"), smtplib.SMTPDataError),
        ("send", smtplib.SMTPNotSupportedError("no SMTPUTF8"), smtplib.SMTPNotSupportedError),
    ],
)
def test_server_problems_stop_the_run_and_mail_problems_fail_the_row(
    smtp: type[FakeSmtp], step: str, error: Exception, raised: type[Exception]
) -> None:
    smtp.errors[step] = error
    login = settings(smtp_starttls=True, smtp_username="apikey")
    with pytest.raises(raised) as caught:
        SmtpMailer(login).send(mail(EmailKind.VERIFY_EMAIL))
    assert type(caught.value) is raised
    assert smtp.calls[-1] == "close"


def test_send_emails_command_delivers_a_working_link(
    db: Session,
    make_user: Callable[..., User],
    smtp: type[FakeSmtp],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    row = EmailService.queue_email(make_user(), EmailKind.VERIFY_EMAIL, db)
    db.commit()
    monkeypatch.setattr(app.cli, "SessionLocal", lambda: nullcontext(db))
    monkeypatch.setattr(app.cli, "get_settings", settings)
    assert app.cli.main(["send-emails"]) == 0
    (message,) = smtp.sent
    assert message["To"] == row.to_email
    db.refresh(row)
    assert row.status == EmailStatus.SENT
    # the token in the link is the one stored (hashed) for the user, so the link works
    token = re.search(r"token=([\w-]+)", message.get_content())
    assert token is not None
    assert EmailService.consume_token(token[1], EmailKind.VERIFY_EMAIL, db).user_id == row.user_id


def test_an_unreachable_server_pauses_the_queue_without_using_attempts(
    db: Session,
    make_user: Callable[..., User],
    smtp: type[FakeSmtp],
) -> None:
    rows = [EmailService.queue_email(make_user(), EmailKind.RESET_PASSWORD, db) for _ in range(3)]
    db.commit()
    smtp.errors["connect"] = ConnectionRefusedError(61, "refused")
    report = send_due_emails(SmtpMailer(settings()), db)
    assert report == SendReport(server_unavailable=True)
    now = db.execute(select(func.now())).scalar_one()
    for row in rows:
        db.refresh(row)
        assert (row.attempts, row.next_attempt_at) == (0, now + SERVER_PAUSE)
