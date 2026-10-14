"""The send-emails job against a fake mailer. The job commits, so tests commit their setup first."""

from collections.abc import Callable
from datetime import datetime, timedelta

import pytest
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models.accounts import User
from app.models.email import MAX_EMAIL_ATTEMPTS, EmailOutbox, EmailToken
from app.models.enums import EmailKind, EmailStatus
from app.services.email import EmailService
from app.services.email_sender import (
    QUOTA_PAUSE,
    MailQuotaError,
    OutgoingMail,
    SendReport,
    retry_delay,
    send_due_emails,
)


class FakeMailer:
    def __init__(self, error: Exception | None = None) -> None:
        self.sent: list[OutgoingMail] = []
        self.error = error

    def send(self, mail: OutgoingMail) -> None:
        if self.error is not None:
            raise self.error
        self.sent.append(mail)


def queue(db: Session, user: User, kind: EmailKind, **fields: object) -> EmailOutbox:
    row = EmailService.queue_email(user, kind, db)
    for name, value in fields.items():
        setattr(row, name, value)
    db.commit()
    return row


def db_now(db: Session) -> datetime:
    return db.execute(select(func.now())).scalar_one()


def test_priority_one_goes_first(db: Session, make_user: Callable[..., User]) -> None:
    verify = queue(db, make_user(), EmailKind.VERIFY_EMAIL)
    notice = queue(db, make_user(), EmailKind.EMAIL_CHANGED_NOTICE)
    reset = queue(db, make_user(), EmailKind.RESET_PASSWORD)
    mailer = FakeMailer()
    assert send_due_emails(db, mailer) == SendReport(sent=3)
    first, second, last = (m.to_email for m in mailer.sent)
    assert {first, second} == {notice.to_email, reset.to_email} and last == verify.to_email
    assert {row.status for row in (verify, notice, reset)} == {EmailStatus.SENT}
    assert all(row.sent_at is not None for row in (verify, notice, reset))


def test_the_link_token_is_made_when_the_mail_is_sent(
    db: Session, make_user: Callable[..., User]
) -> None:
    user = make_user()
    queue(db, user, EmailKind.RESET_PASSWORD)
    assert db.scalar(select(func.count()).select_from(EmailToken)) == 0
    mailer = FakeMailer()
    send_due_emails(db, mailer)
    (mail,) = mailer.sent
    assert mail.kind == EmailKind.RESET_PASSWORD and mail.token is not None
    token = EmailService.consume_token(mail.token, EmailKind.RESET_PASSWORD, db)
    assert token.user_id == user.id


def test_change_email_link_is_for_the_new_address(
    db: Session, make_user: Callable[..., User]
) -> None:
    row = EmailService.queue_email(
        make_user(), EmailKind.CHANGE_EMAIL, db, to_email="new@example.com"
    )
    db.commit()
    mailer = FakeMailer()
    send_due_emails(db, mailer)
    assert row.status == EmailStatus.SENT
    raw = mailer.sent[0].token
    assert raw is not None
    assert EmailService.consume_token(raw, EmailKind.CHANGE_EMAIL, db).new_email == row.to_email


@pytest.mark.parametrize("kind", [EmailKind.EMAIL_CHANGED_NOTICE, EmailKind.TA_INVITATION])
def test_mail_without_a_link_gets_no_token(
    db: Session, make_user: Callable[..., User], kind: EmailKind
) -> None:
    row = EmailService.queue_email(make_user(), kind, db, params={"class_name": "JAPN 101"})
    db.commit()
    mailer = FakeMailer()
    send_due_emails(db, mailer)
    assert mailer.sent == [OutgoingMail(row.to_email, kind, {"class_name": "JAPN 101"}, None)]
    assert db.scalar(select(func.count()).select_from(EmailToken)) == 0


def test_a_failed_send_waits_and_keeps_the_older_link(
    db: Session, make_user: Callable[..., User]
) -> None:
    user = make_user()
    older = EmailService.create_token(user, EmailKind.VERIFY_EMAIL, db)
    row = queue(db, user, EmailKind.VERIFY_EMAIL)
    report = send_due_emails(db, FakeMailer(OSError("connection refused")))
    assert report == SendReport(retried=1)
    assert (row.status, row.attempts) == (EmailStatus.PENDING, 1)
    assert row.next_attempt_at == db_now(db) + retry_delay(1)
    # the failed send's token was rolled back, so the link already sent still works
    assert db.scalar(select(func.count()).select_from(EmailToken)) == 1
    assert EmailService.consume_token(older, EmailKind.VERIFY_EMAIL, db).user_id == user.id
    # not due again yet
    assert send_due_emails(db, FakeMailer()) == SendReport()


def test_the_last_failed_attempt_gives_up(db: Session, make_user: Callable[..., User]) -> None:
    user = make_user()
    row = queue(db, user, EmailKind.RESET_PASSWORD, attempts=MAX_EMAIL_ATTEMPTS - 1)
    assert send_due_emails(db, FakeMailer(OSError())) == SendReport(failed=1)
    assert (row.status, row.attempts) == (EmailStatus.FAILED, MAX_EMAIL_ATTEMPTS)
    # a failed reset no longer blocks a new request
    assert EmailService.queue_email(user, EmailKind.RESET_PASSWORD, db).id != row.id


def test_a_sending_limit_pauses_the_queue_without_counting(
    db: Session, make_user: Callable[..., User]
) -> None:
    rows = [queue(db, make_user(), EmailKind.VERIFY_EMAIL) for _ in range(2)]
    later = queue(db, make_user(), EmailKind.VERIFY_EMAIL)
    later.next_attempt_at = func.now() + QUOTA_PAUSE + timedelta(minutes=5)
    db.commit()
    report = send_due_emails(db, FakeMailer(MailQuotaError()))
    assert report == SendReport(quota_reached=True)
    pause_end = db_now(db) + QUOTA_PAUSE
    for row in rows:
        db.refresh(row)
        assert (row.status, row.attempts, row.next_attempt_at) == (
            EmailStatus.PENDING,
            0,
            pause_end,
        )
    db.refresh(later)
    assert later.next_attempt_at == pause_end + timedelta(minutes=5)  # not moved earlier
    assert db.scalar(select(func.count()).select_from(EmailToken)) == 0
    assert send_due_emails(db, FakeMailer()) == SendReport()  # nothing due during the pause


def test_an_error_before_sending_counts_as_a_failed_attempt(
    db: Session, make_user: Callable[..., User], monkeypatch: pytest.MonkeyPatch
) -> None:
    row = queue(db, make_user(), EmailKind.RESET_PASSWORD)

    def broken(*args: object, **kwargs: object) -> str:
        raise RuntimeError("token table unavailable")

    monkeypatch.setattr(EmailService, "create_token", broken)
    mailer = FakeMailer()
    assert send_due_emails(db, mailer) == SendReport(retried=1)
    assert mailer.sent == []
    db.refresh(row)
    assert (row.status, row.attempts) == (EmailStatus.PENDING, 1)
    assert send_due_emails(db, mailer) == SendReport()  # waits instead of failing every minute


def test_only_due_rows_are_sent_and_at_most_the_limit(
    db: Session, make_user: Callable[..., User]
) -> None:
    later = queue(db, make_user(), EmailKind.RESET_PASSWORD)
    later.next_attempt_at = func.now() + timedelta(minutes=5)
    db.commit()
    due = [queue(db, make_user(), EmailKind.VERIFY_EMAIL) for _ in range(3)]
    mailer = FakeMailer()
    assert send_due_emails(db, mailer, limit=2) == SendReport(sent=2)
    assert {m.to_email for m in mailer.sent} < {row.to_email for row in due}
    assert later.status == EmailStatus.PENDING


def test_a_sent_row_is_sent_once(db: Session, make_user: Callable[..., User]) -> None:
    queue(db, make_user(), EmailKind.VERIFY_EMAIL)
    mailer = FakeMailer()
    send_due_emails(db, mailer)
    send_due_emails(db, mailer)
    assert len(mailer.sent) == 1


@pytest.mark.parametrize("kind", [EmailKind.VERIFY_EMAIL, EmailKind.SET_PASSWORD])
def test_mail_to_an_address_the_user_no_longer_has_is_not_sent(
    db: Session, make_user: Callable[..., User], kind: EmailKind
) -> None:
    user = make_user()
    old = queue(db, user, kind)
    user.email = "corrected@example.com"  # e.g. an admin fixed a typo
    db.commit()
    new = queue(db, user, kind)
    mailer = FakeMailer()
    assert send_due_emails(db, mailer) == SendReport(sent=1, failed=1)
    # only the current address gets a link, so the old row can't replace it
    assert [m.to_email for m in mailer.sent] == ["corrected@example.com"]
    assert (old.status, new.status) == (EmailStatus.FAILED, EmailStatus.SENT)
