"""The send-emails job against a fake mailer. The job commits, so tests commit their setup first."""

import logging
import threading
from collections.abc import Callable, Iterator
from datetime import datetime, timedelta
from typing import Any
from uuid import UUID, uuid4

import pytest
from sqlalchemy import Engine, delete, func, select, text
from sqlalchemy.orm import Session

from app.accounts.email_links import request_password_reset, reset_password
from app.errors import AppError
from app.models.accounts import RefreshToken, User
from app.models.email import MAX_EMAIL_ATTEMPTS, EmailOutbox, EmailToken
from app.models.enums import EmailKind, EmailStatus, UserRole
from app.services import email_sender
from app.services.email import EmailService
from app.services.email_sender import (
    QUOTA_PAUSE,
    MailQuotaError,
    OutgoingMail,
    SendReport,
    retry_delay,
    send_due_emails,
)

NEW_PASSWORD = "NewPassword1"
WAIT_S = 10  # every wait between threads gives up after this


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


def tokens(db: Session, *user_ids: UUID) -> int:
    """Link tokens of these users only, so links left in a dev database don't count."""
    query = select(func.count()).where(EmailToken.user_id.in_(user_ids))
    return db.execute(query).scalar_one()


def test_priority_one_goes_first(db: Session, make_user: Callable[..., User]) -> None:
    verify = queue(db, make_user(), EmailKind.VERIFY_EMAIL)
    notice = queue(db, make_user(), EmailKind.EMAIL_CHANGED_NOTICE)
    reset = queue(db, make_user(), EmailKind.RESET_PASSWORD)
    mailer = FakeMailer()
    assert send_due_emails(mailer, db) == SendReport(sent=3)
    first, second, last = (m.to_email for m in mailer.sent)
    assert {first, second} == {notice.to_email, reset.to_email} and last == verify.to_email
    assert {row.status for row in (verify, notice, reset)} == {EmailStatus.SENT}
    assert all(row.sent_at is not None for row in (verify, notice, reset))


def test_the_link_token_is_made_when_the_mail_is_sent(
    db: Session, make_user: Callable[..., User]
) -> None:
    user = make_user()
    queue(db, user, EmailKind.RESET_PASSWORD)
    assert tokens(db, user.id) == 0
    mailer = FakeMailer()
    send_due_emails(mailer, db)
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
    send_due_emails(mailer, db)
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
    send_due_emails(mailer, db)
    assert mailer.sent == [OutgoingMail(row.to_email, kind, {"class_name": "JAPN 101"}, None)]
    assert tokens(db, row.user_id) == 0


def test_a_failed_send_waits_and_keeps_the_older_link(
    db: Session, make_user: Callable[..., User]
) -> None:
    user = make_user()
    older = EmailService.create_token(user, EmailKind.VERIFY_EMAIL, db)
    row = queue(db, user, EmailKind.VERIFY_EMAIL)
    report = send_due_emails(FakeMailer(OSError("connection refused")), db)
    assert report == SendReport(retried=1)
    assert (row.status, row.attempts) == (EmailStatus.PENDING, 1)
    assert row.next_attempt_at == db_now(db) + retry_delay(1)
    # the failed send's token was rolled back, so the link already sent still works
    assert tokens(db, user.id) == 1
    assert EmailService.consume_token(older, EmailKind.VERIFY_EMAIL, db).user_id == user.id
    # not due again yet
    assert send_due_emails(FakeMailer(), db) == SendReport()


def test_the_last_failed_attempt_gives_up(db: Session, make_user: Callable[..., User]) -> None:
    user = make_user()
    row = queue(db, user, EmailKind.RESET_PASSWORD, attempts=MAX_EMAIL_ATTEMPTS - 1)
    assert send_due_emails(FakeMailer(OSError()), db) == SendReport(failed=1)
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
    report = send_due_emails(FakeMailer(MailQuotaError()), db)
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
    assert tokens(db, *(row.user_id for row in [*rows, later])) == 0
    assert send_due_emails(FakeMailer(), db) == SendReport()  # nothing due during the pause


def test_an_error_before_sending_counts_as_a_failed_attempt(
    db: Session, make_user: Callable[..., User], monkeypatch: pytest.MonkeyPatch
) -> None:
    row = queue(db, make_user(), EmailKind.RESET_PASSWORD)

    def broken(*args: object, **kwargs: object) -> str:
        raise RuntimeError("token table unavailable")

    monkeypatch.setattr(EmailService, "create_token", broken)
    mailer = FakeMailer()
    assert send_due_emails(mailer, db) == SendReport(retried=1)
    assert mailer.sent == []
    db.refresh(row)
    assert (row.status, row.attempts) == (EmailStatus.PENDING, 1)
    assert send_due_emails(mailer, db) == SendReport()  # waits instead of failing every minute


def test_only_due_rows_are_sent_and_at_most_the_limit(
    db: Session, make_user: Callable[..., User]
) -> None:
    later = queue(db, make_user(), EmailKind.RESET_PASSWORD)
    later.next_attempt_at = func.now() + timedelta(minutes=5)
    db.commit()
    due = [queue(db, make_user(), EmailKind.VERIFY_EMAIL) for _ in range(3)]
    mailer = FakeMailer()
    assert send_due_emails(mailer, db, limit=2) == SendReport(sent=2)
    assert {m.to_email for m in mailer.sent} < {row.to_email for row in due}
    assert later.status == EmailStatus.PENDING


def test_a_sent_row_is_sent_once(db: Session, make_user: Callable[..., User]) -> None:
    queue(db, make_user(), EmailKind.VERIFY_EMAIL)
    mailer = FakeMailer()
    send_due_emails(mailer, db)
    send_due_emails(mailer, db)
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
    assert send_due_emails(mailer, db) == SendReport(sent=1, failed=1)
    # only the current address gets a link, so the old row can't replace it
    assert [m.to_email for m in mailer.sent] == ["corrected@example.com"]
    assert (old.status, new.status) == (EmailStatus.FAILED, EmailStatus.SENT)


def test_a_reset_to_an_old_address_is_not_sent(db: Session, make_user: Callable[..., User]) -> None:
    user = make_user()
    row = queue(db, user, EmailKind.RESET_PASSWORD)
    user.email = "corrected@example.com"
    db.commit()
    assert send_due_emails(FakeMailer(), db) == SendReport(failed=1)
    assert row.status == EmailStatus.FAILED


def test_the_address_check_reads_the_database_not_the_session(
    db: Session, make_user: Callable[..., User], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(db, "expire_on_commit", False)  # like SessionLocal
    user = make_user()
    row = queue(db, user, EmailKind.VERIFY_EMAIL)
    # changed outside this session: the User object here still has the old address
    db.execute(text("UPDATE users SET email = 'moved@example.com' WHERE id = :id"), {"id": user.id})
    db.commit()
    assert send_due_emails(FakeMailer(), db) == SendReport(failed=1)
    db.refresh(row)
    assert row.status == EmailStatus.FAILED


@pytest.mark.parametrize("newer_status", [EmailStatus.PENDING, EmailStatus.FAILED])
def test_an_older_mail_is_skipped_unless_the_newer_one_failed(
    db: Session, make_user: Callable[..., User], newer_status: EmailStatus
) -> None:
    user = make_user()
    typo = EmailService.queue_email(user, EmailKind.CHANGE_EMAIL, db, to_email="typo@example.com")
    typo.created_at = func.now() - timedelta(minutes=5)  # tests share one transaction's now()
    typo.next_attempt_at = func.now() + timedelta(minutes=2)  # its first send failed
    right = EmailService.queue_email(user, EmailKind.CHANGE_EMAIL, db, to_email="right@example.com")
    right.status = newer_status  # failed: e.g. it gave up after the last attempt
    db.commit()
    mailer = FakeMailer()
    send_due_emails(mailer, db)  # sends the newer one if it's pending
    typo.next_attempt_at = func.now()  # the retry is due
    db.commit()
    skipped = newer_status == EmailStatus.PENDING
    assert send_due_emails(mailer, db) == (SendReport(failed=1) if skipped else SendReport(sent=1))
    assert typo.status == (EmailStatus.FAILED if skipped else EmailStatus.SENT)
    # the link sent last still works
    used = EmailService.consume_token(mailer.sent[-1].token or "", EmailKind.CHANGE_EMAIL, db)
    assert used.new_email == (right.to_email if skipped else typo.to_email)


def test_tokens_and_addresses_stay_out_of_repr_and_logs(
    db: Session, make_user: Callable[..., User], caplog: pytest.LogCaptureFixture
) -> None:
    mail = OutgoingMail("a@example.com", EmailKind.RESET_PASSWORD, {}, "secret-token")
    assert "secret-token" not in repr(mail)
    row = queue(db, make_user(), EmailKind.EMAIL_CHANGED_NOTICE)
    with caplog.at_level(logging.ERROR):
        send_due_emails(FakeMailer(RuntimeError(f"refused {row.to_email}")), db)
    assert str(row.id) in caplog.text and row.to_email not in caplog.text


# A reset mail queued before a password reset must never go out after it (#199): its link
# would work for 60 minutes after the password changed.


def reset_user(make_user: Callable[..., User]) -> User:
    return make_user(email_verified_at=func.now())


def test_a_reset_marks_a_waiting_reset_mail_so_it_is_not_sent(
    db: Session, make_user: Callable[..., User]
) -> None:
    user = reset_user(make_user)
    link = EmailService.create_token(user, EmailKind.RESET_PASSWORD, db)
    row = queue(db, user, EmailKind.RESET_PASSWORD)
    reset_password(link, NEW_PASSWORD, db)
    db.commit()
    mailer = FakeMailer()
    assert send_due_emails(mailer, db) == SendReport()
    assert (mailer.sent, row.status, tokens(db, user.id)) == ([], EmailStatus.FAILED, 1)


def test_a_reset_mail_sent_first_replaces_the_older_link(
    db: Session, make_user: Callable[..., User]
) -> None:
    user = reset_user(make_user)
    older = EmailService.create_token(user, EmailKind.RESET_PASSWORD, db)
    queue(db, user, EmailKind.RESET_PASSWORD)
    mailer = FakeMailer()
    assert send_due_emails(mailer, db) == SendReport(sent=1)
    with pytest.raises(AppError):  # the mail's new link replaced it
        reset_password(older, NEW_PASSWORD, db)
    reset_password(mailer.sent[0].token or "", NEW_PASSWORD, db)


def test_a_reset_mail_queued_after_a_reset_is_sent(
    db: Session, make_user: Callable[..., User]
) -> None:
    user = reset_user(make_user)
    reset_password(EmailService.create_token(user, EmailKind.RESET_PASSWORD, db), NEW_PASSWORD, db)
    queue(db, user, EmailKind.RESET_PASSWORD)  # e.g. forgot the new password too
    mailer = FakeMailer()
    assert send_due_emails(mailer, db) == SendReport(sent=1)


class Step:
    """One thread's job, with its error kept and a timeout on every wait, so a broken order
    fails the test instead of hanging the suite."""

    def __init__(self, job: Callable[[], object]) -> None:
        self.error: BaseException | None = None

        def run() -> None:
            try:
                job()
            except BaseException as error:  # reported by finish()
                self.error = error

        self.thread = threading.Thread(target=run, daemon=True)
        self.thread.start()

    def finish(self) -> None:
        self.thread.join(timeout=WAIT_S)
        assert not self.thread.is_alive(), "a thread is stuck"
        if self.error is not None:
            raise self.error


def wait(event: threading.Event) -> None:
    assert event.wait(timeout=WAIT_S), "the other thread never got there"


@pytest.fixture
def committed_user(test_engine: Engine) -> Iterator[User]:
    """A verified user committed for real, so other connections see it; removed afterwards."""
    with Session(test_engine, expire_on_commit=False) as setup:
        user = User(
            email=f"{uuid4().hex[:12]}@example.com",
            username=f"user_{uuid4().hex[:8]}",
            role=UserRole.STUDENT,
            email_verified_at=func.now(),
        )
        setup.add(user)
        setup.commit()
    yield user
    with Session(test_engine) as cleanup:
        cleanup.execute(text("SET lock_timeout = '10s'"))
        for table in (EmailOutbox, EmailToken, RefreshToken):
            cleanup.execute(delete(table).where(table.user_id == user.id))
        cleanup.execute(delete(User).where(User.id == user.id))
        cleanup.commit()


@pytest.fixture
def send_job_holding_the_mail(
    test_engine: Engine, committed_user: User, monkeypatch: pytest.MonkeyPatch
) -> Iterator[Callable[[], tuple[Step, threading.Event, threading.Event, FakeMailer]]]:
    """Starts the send job in a thread; it locks the user's due mail, signals `holding`, then
    waits for `go_on` before it locks the user and makes the link. The job only sees this
    user's mail, so it can't pick up (and commit as sent) another due row in the database.
    Teardown releases the job and waits for it, even when the test failed half way."""
    started: list[tuple[Step, threading.Event]] = []

    def lock_this_users_mail(db: Session) -> EmailOutbox | None:
        return db.scalars(
            select(EmailOutbox)
            .where(
                EmailOutbox.user_id == committed_user.id,
                EmailOutbox.status == EmailStatus.PENDING,
            )
            .limit(1)
            .with_for_update(skip_locked=True),
            execution_options={"populate_existing": True},
        ).one_or_none()

    monkeypatch.setattr(email_sender, "_lock_next_due", lock_this_users_mail)

    def start() -> tuple[Step, threading.Event, threading.Event, FakeMailer]:
        holding, go_on, mailer = threading.Event(), threading.Event(), FakeMailer()
        make_link = email_sender._token_for

        def held(row: EmailOutbox, db: Session) -> str | None:
            holding.set()
            wait(go_on)
            return make_link(row, db)

        monkeypatch.setattr(email_sender, "_token_for", held)

        def job() -> None:
            with Session(test_engine) as session:
                session.execute(text("SET lock_timeout = '10s'"))
                send_due_emails(mailer, session, limit=1)

        step = Step(job)
        started.append((step, go_on))
        return step, holding, go_on, mailer

    yield start
    for step, go_on in started:
        go_on.set()
        step.thread.join(timeout=WAIT_S)


def outcome(test_engine: Engine, user: User) -> tuple[list[str], int]:
    with Session(test_engine) as check:
        statuses = check.scalars(select(EmailOutbox.status).where(EmailOutbox.user_id == user.id))
        return list(statuses), tokens(check, user.id)


def test_a_reset_while_the_job_holds_the_mail_stops_it(
    test_engine: Engine,
    committed_user: User,
    send_job_holding_the_mail: Callable[..., tuple[Step, threading.Event, threading.Event, Any]],
) -> None:
    with Session(test_engine) as setup:
        link = EmailService.create_token(committed_user, EmailKind.RESET_PASSWORD, setup)
        EmailService.queue_email(committed_user, EmailKind.RESET_PASSWORD, setup)
        setup.commit()
    sending, holding, go_on, mailer = send_job_holding_the_mail()
    wait(holding)

    with Session(test_engine) as reset:  # skips the mail the job holds (SKIP LOCKED)
        reset_password(link, NEW_PASSWORD, reset)
        reset.commit()
    go_on.set()
    sending.finish()

    assert mailer.sent == []
    assert outcome(test_engine, committed_user) == ([EmailStatus.FAILED], 1)


def test_a_reset_that_began_before_the_mail_was_queued_still_stops_it(
    test_engine: Engine,
    committed_user: User,
    monkeypatch: pytest.MonkeyPatch,
    send_job_holding_the_mail: Callable[..., tuple[Step, threading.Event, threading.Event, Any]],
) -> None:
    """The reset's transaction starts first (its now() is earlier than the mail's), but it uses
    the link only after the mail was queued, while the send job holds that mail."""
    with Session(test_engine) as setup:
        link = EmailService.create_token(committed_user, EmailKind.RESET_PASSWORD, setup)
        setup.commit()
    hashing, hash_now = threading.Event(), threading.Event()

    def slow_hash(password: str) -> str:
        hashing.set()
        wait(hash_now)
        return "$2b$12$" + "x" * 53

    monkeypatch.setattr("app.accounts.email_links.hash_password", slow_hash)

    def reset() -> None:
        with Session(test_engine) as session:
            session.execute(text("SET lock_timeout = '10s'"))
            reset_password(link, NEW_PASSWORD, session)
            session.commit()

    resetting = Step(reset)
    try:
        wait(hashing)  # the reset's transaction has begun
        with Session(test_engine) as request:
            request_password_reset(committed_user.email, request)
            request.commit()
        sending, holding, go_on, mailer = send_job_holding_the_mail()
        wait(holding)
    finally:
        hash_now.set()  # never leave the reset waiting
    resetting.finish()
    go_on.set()
    sending.finish()

    assert mailer.sent == []
    assert outcome(test_engine, committed_user) == ([EmailStatus.FAILED], 1)
