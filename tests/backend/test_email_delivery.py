"""Email templates, the SMTP mailer and the send-emails command."""

import re
import smtplib
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
    connect_error: Exception | None = None
    error: Exception | None = None
    quit_error: Exception | None = None

    def __init__(self, host: str, port: int, timeout: float) -> None:
        if self.connect_error is not None:
            raise self.connect_error
        self.calls.append(f"connect {host}:{port}")

    def starttls(self) -> None:
        self.calls.append("starttls")

    def login(self, user: str, password: str) -> None:
        self.calls.append(f"login {user}")

    def send_message(self, message: EmailMessage) -> None:
        if self.error is not None:
            raise self.error
        self.sent.append(message)

    def quit(self) -> None:
        self.calls.append("quit")
        if self.quit_error is not None:
            raise self.quit_error

    def close(self) -> None:
        self.calls.append("close")


@pytest.fixture
def smtp(monkeypatch: pytest.MonkeyPatch) -> type[FakeSmtp]:
    FakeSmtp.calls, FakeSmtp.sent = [], []
    FakeSmtp.connect_error = FakeSmtp.error = FakeSmtp.quit_error = None
    monkeypatch.setattr(smtplib, "SMTP", FakeSmtp)
    return FakeSmtp


def test_smtp_mailer_sends_the_rendered_email(smtp: type[FakeSmtp]) -> None:
    settings = Settings(smtp_host="mailpit", smtp_port=1025, mail_from="Hunt <a@b.ca>")
    SmtpMailer(settings).send(mail(EmailKind.RESET_PASSWORD))
    assert smtp.calls == ["connect mailpit:1025", "quit"]  # Mailpit: no TLS, no login
    (message,) = smtp.sent
    assert (message["From"], message["To"]) == ("Hunt <a@b.ca>", "student@example.com")
    assert message["Subject"] == "Reset your password"
    assert message["Date"] and message["Message-ID"].endswith("@b.ca>")  # spam filters want both
    assert "token=tok-en_1" in message.get_content()


def test_smtp_mailer_uses_starttls_and_login_when_set(smtp: type[FakeSmtp]) -> None:
    settings = Settings(smtp_starttls=True, smtp_username="apikey", smtp_password="secret")
    SmtpMailer(settings).send(mail(EmailKind.VERIFY_EMAIL))
    assert smtp.calls[1:3] == ["starttls", "login apikey"]


def test_a_failed_quit_after_sending_is_ignored(smtp: type[FakeSmtp]) -> None:
    smtp.quit_error = smtplib.SMTPServerDisconnected("gone")
    SmtpMailer(Settings()).send(mail(EmailKind.VERIFY_EMAIL))  # delivered, so no error
    assert len(smtp.sent) == 1 and smtp.calls[-2:] == ["quit", "close"]


def refused(code: int, reply: bytes) -> smtplib.SMTPRecipientsRefused:
    return smtplib.SMTPRecipientsRefused({"student@example.com": (code, reply)})


@pytest.mark.parametrize(
    ("error", "raised"),
    [
        (smtplib.SMTPDataError(452, b"too many messages"), MailQuotaError),
        (smtplib.SMTPDataError(550, b"5.4.5 Daily user sending limit exceeded"), MailQuotaError),
        (smtplib.SMTPDataError(421, b"try again later"), MailUnavailableError),
        (smtplib.SMTPAuthenticationError(535, b"bad credentials"), MailUnavailableError),
        (smtplib.SMTPServerDisconnected("connection lost"), MailUnavailableError),
        # about one recipient, so only this mail fails
        (refused(452, b"4.2.2 over quota"), smtplib.SMTPRecipientsRefused),
        (refused(550, b"no such user"), smtplib.SMTPRecipientsRefused),
        (smtplib.SMTPDataError(552, b"5.2.2 Mailbox quota exceeded"), smtplib.SMTPDataError),
        (smtplib.SMTPDataError(554, b"message rejected"), smtplib.SMTPDataError),
    ],
    ids=[
        "452",
        "550 5.4.5",
        "421",
        "535 login",
        "disconnected",
        "refused over quota",
        "refused unknown",
        "552 mailbox full",
        "554",
    ],
)
def test_server_problems_stop_the_run_and_mail_problems_fail_the_row(
    smtp: type[FakeSmtp], error: Exception, raised: type[Exception]
) -> None:
    smtp.error = error
    with pytest.raises(raised) as caught:
        SmtpMailer(Settings()).send(mail(EmailKind.VERIFY_EMAIL))
    assert type(caught.value) is raised
    assert smtp.calls[-1] == "close"


@pytest.mark.parametrize(
    "error",
    [ConnectionRefusedError(61, "refused"), TimeoutError("timed out"), OSError("no such host")],
    ids=["refused", "timeout", "dns"],
)
def test_an_unreachable_server_is_unavailable(smtp: type[FakeSmtp], error: Exception) -> None:
    smtp.connect_error = error
    with pytest.raises(MailUnavailableError) as caught:
        SmtpMailer(Settings()).send(mail(EmailKind.VERIFY_EMAIL))
    assert type(caught.value) is MailUnavailableError


def test_send_emails_command_delivers_a_working_link(
    db: Session,
    make_user: Callable[..., User],
    smtp: type[FakeSmtp],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    row = EmailService.queue_email(make_user(), EmailKind.VERIFY_EMAIL, db)
    db.commit()
    monkeypatch.setattr(app.cli, "SessionLocal", lambda: nullcontext(db))
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
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    rows = [EmailService.queue_email(make_user(), EmailKind.RESET_PASSWORD, db) for _ in range(3)]
    db.commit()
    smtp.connect_error = ConnectionRefusedError(61, "refused")
    report = send_due_emails(db, SmtpMailer(Settings()))
    assert report == SendReport(server_unavailable=True)
    now = db.execute(select(func.now())).scalar_one()
    for row in rows:
        db.refresh(row)
        assert (row.attempts, row.next_attempt_at) == (0, now + SERVER_PAUSE)


def test_unknown_command_is_rejected() -> None:
    with pytest.raises(SystemExit):
        app.cli.main(["send-everything"])
