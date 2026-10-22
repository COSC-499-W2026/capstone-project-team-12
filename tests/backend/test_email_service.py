"""EmailService queues mail and checks hashed link tokens. It does not send."""

import hashlib
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from http import HTTPStatus

import pytest
from sqlalchemy import func, select, text
from sqlalchemy.orm import Session

from app.errors import AppError, ErrorCode
from app.models.accounts import User
from app.models.email import TOKEN_KINDS, EmailOutbox, EmailToken, priority_for
from app.models.enums import EmailKind, EmailStatus
from app.services.email import TOKEN_TTL, EmailService


def test_every_link_kind_has_a_lifetime_and_tokens_are_sha256() -> None:
    assert set(TOKEN_TTL) == set(TOKEN_KINDS)
    assert TOKEN_TTL[EmailKind.RESET_PASSWORD] == timedelta(minutes=60)  # spec, email_tokens
    assert TOKEN_TTL[EmailKind.VERIFY_EMAIL] == timedelta(days=7)
    assert TOKEN_TTL[EmailKind.SET_PASSWORD] == timedelta(days=3)
    assert TOKEN_TTL[EmailKind.CHANGE_EMAIL] == timedelta(hours=24)
    assert EmailService.hash_token("abc") == hashlib.sha256(b"abc").hexdigest()


@pytest.mark.parametrize("kind", list(EmailKind))
def test_queue_email_sets_priority_and_stores_no_token(
    db: Session, make_user: Callable[..., User], kind: EmailKind
) -> None:
    user = make_user()
    row = EmailService.queue_email(user, kind, db)
    assert (row.to_email, row.template, row.priority) == (user.email, kind, priority_for(kind))
    assert row.status == EmailStatus.PENDING and row.attempts == 0 and row.params == {}
    assert row.sent_at is None
    tokens = select(func.count()).where(EmailToken.user_id == user.id)
    assert db.scalar(tokens) == 0


def test_second_pending_reset_returns_the_queued_row(
    db: Session, make_user: Callable[..., User]
) -> None:
    user = make_user()
    first = EmailService.queue_email(user, EmailKind.RESET_PASSWORD, db)
    again = EmailService.queue_email(user, EmailKind.RESET_PASSWORD, db)
    assert again.id == first.id
    first.status, first.sent_at = EmailStatus.SENT, datetime.now(UTC)
    db.flush()
    assert EmailService.queue_email(user, EmailKind.RESET_PASSWORD, db).id != first.id
    resets = select(func.count()).where(
        EmailOutbox.user_id == user.id, EmailOutbox.template == EmailKind.RESET_PASSWORD
    )
    assert db.scalar(resets) == 2


def test_reset_sent_between_conflict_and_read_is_queued_again(
    db: Session, make_user: Callable[..., User], monkeypatch: pytest.MonkeyPatch
) -> None:
    user = make_user()
    first = EmailService.queue_email(user, EmailKind.RESET_PASSWORD, db)
    real_scalars = db.scalars
    calls = {"n": 0}

    def send_first_then_read(*args: object, **kwargs: object) -> object:
        calls["n"] += 1
        if calls["n"] == 2:  # the UPDATE after the conflict: the job sent it meanwhile
            first.status, first.sent_at = EmailStatus.SENT, datetime.now(UTC)
            db.flush()
        return real_scalars(*args, **kwargs)  # type: ignore[call-overload]

    monkeypatch.setattr(db, "scalars", send_first_then_read)
    again = EmailService.queue_email(user, EmailKind.RESET_PASSWORD, db)
    assert again.id != first.id and again.status == EmailStatus.PENDING


def test_a_pending_reset_does_not_block_other_mail(
    db: Session, make_user: Callable[..., User]
) -> None:
    user = make_user()
    reset = EmailService.queue_email(user, EmailKind.RESET_PASSWORD, db)
    verify = EmailService.queue_email(user, EmailKind.VERIFY_EMAIL, db)
    assert verify.id != reset.id
    pending = select(func.count()).where(
        EmailOutbox.user_id == user.id, EmailOutbox.status == EmailStatus.PENDING
    )
    assert db.scalar(pending) == 2


def test_a_repeated_reset_goes_to_the_current_address(
    db: Session, make_user: Callable[..., User]
) -> None:
    user = make_user()
    first = EmailService.queue_email(user, EmailKind.RESET_PASSWORD, db)
    user.email = "changed@example.com"
    db.flush()
    first.attempts, first.next_attempt_at = 2, datetime.now(UTC) + timedelta(hours=1)
    db.flush()
    again = EmailService.queue_email(user, EmailKind.RESET_PASSWORD, db)
    assert again.id == first.id and again.to_email == "changed@example.com"
    assert again.attempts == 0 and again.next_attempt_at <= datetime.now(UTC)
    again.attempts = 1
    db.flush()
    # same address again: the row is returned unchanged
    assert EmailService.queue_email(user, EmailKind.RESET_PASSWORD, db).attempts == 1


def test_queue_email_takes_an_address_and_params(
    db: Session, make_user: Callable[..., User]
) -> None:
    user = make_user()
    row = EmailService.queue_email(
        user, EmailKind.CHANGE_EMAIL, db, to_email=" New@x.com ", params={"name": "Mia"}
    )
    assert (row.to_email, row.params) == ("new@x.com", {"name": "Mia"})
    hour = timedelta(hours=1)
    assert EmailService.count_recent("new@x.com", EmailKind.CHANGE_EMAIL, hour, db) == 1
    for blank in ("", " "):  # not silently sent to user.email, and never stored empty
        with pytest.raises(ValueError, match="empty"):
            EmailService.queue_email(user, EmailKind.VERIFY_EMAIL, db, to_email=blank)
    with pytest.raises(ValueError, match="token"):
        EmailService.queue_email(user, EmailKind.VERIFY_EMAIL, db, params={"token": "x"})
    first = EmailService.queue_email(user, EmailKind.RESET_PASSWORD, db, params={"n": 1})
    again = EmailService.queue_email(user, EmailKind.RESET_PASSWORD, db, params={"n": 2})
    assert again.id == first.id and again.params == {"n": 1}  # the queued message is kept


def test_many_calls_on_one_connection_keep_working(
    db: Session, make_user: Callable[..., User]
) -> None:
    # psycopg prepares a statement after 5 runs and Postgres may switch to a generic
    # plan later; ON CONFLICT must still match the partial index then
    user = make_user()
    for i in range(15):
        kind = EmailKind.RESET_PASSWORD if i % 2 else EmailKind.VERIFY_EMAIL
        assert EmailService.queue_email(user, kind, db).status == EmailStatus.PENDING
    resets = select(func.count()).where(
        EmailOutbox.user_id == user.id, EmailOutbox.template == EmailKind.RESET_PASSWORD
    )
    assert db.scalar(resets) == 1


def test_a_new_link_replaces_the_older_one_of_the_same_kind(
    db: Session, make_user: Callable[..., User]
) -> None:
    user, other = make_user(), make_user()
    older = EmailService.create_token(user, EmailKind.RESET_PASSWORD, db)
    verify = EmailService.create_token(user, EmailKind.VERIFY_EMAIL, db)
    others = EmailService.create_token(other, EmailKind.RESET_PASSWORD, db)
    newer = EmailService.create_token(user, EmailKind.RESET_PASSWORD, db)
    with pytest.raises(AppError):
        EmailService.consume_token(older, EmailKind.RESET_PASSWORD, db)
    # only this user's links of this kind stop working
    assert EmailService.consume_token(newer, EmailKind.RESET_PASSWORD, db).user_id == user.id
    used = EmailService.consume_token(verify, EmailKind.VERIFY_EMAIL, db)
    used.used_at = earlier = datetime.now(UTC) - timedelta(days=1)  # tests share one now()
    db.flush()
    EmailService.create_token(user, EmailKind.VERIFY_EMAIL, db)  # must not touch a used link
    db.refresh(used)
    assert (used.user_id, used.used_at) == (user.id, earlier)
    assert EmailService.consume_token(others, EmailKind.RESET_PASSWORD, db).user_id == other.id


def test_count_recent_is_per_address_kind_and_window(
    db: Session, make_user: Callable[..., User]
) -> None:
    user = make_user()
    other = make_user()
    now = datetime.now(UTC)

    def at(kind: EmailKind, owner: User, age: timedelta, *, sent: bool = False) -> None:
        row = EmailService.queue_email(owner, kind, db)
        row.created_at = now - age
        if sent:
            row.status = EmailStatus.SENT
            row.sent_at = now
        db.flush()

    at(EmailKind.VERIFY_EMAIL, user, timedelta(minutes=30))
    at(EmailKind.VERIFY_EMAIL, user, timedelta(minutes=1))
    at(EmailKind.VERIFY_EMAIL, user, timedelta(minutes=10), sent=True)
    at(EmailKind.VERIFY_EMAIL, user, timedelta(hours=2))
    at(EmailKind.RESET_PASSWORD, user, timedelta(minutes=1))
    at(EmailKind.VERIFY_EMAIL, other, timedelta(minutes=1))
    window = timedelta(hours=1)
    padded = f"  {user.email.upper()} "  # counted as the same address
    assert EmailService.count_recent(padded, EmailKind.VERIFY_EMAIL, window, db) == 3
    assert EmailService.count_recent(other.email, EmailKind.VERIFY_EMAIL, window, db) == 1
    assert EmailService.count_recent(user.email, EmailKind.RESET_PASSWORD, window, db) == 1
    assert EmailService.count_recent("nobody@example.com", EmailKind.VERIFY_EMAIL, window, db) == 0


def test_create_token_stores_only_the_hash(
    db: Session, make_user: Callable[..., User], transaction_calls: list[str]
) -> None:
    user = make_user()
    EmailService.queue_email(user, EmailKind.RESET_PASSWORD, db)
    raw = EmailService.create_token(user, EmailKind.RESET_PASSWORD, db)
    change = EmailService.create_token(
        user, EmailKind.CHANGE_EMAIL, db, new_email="Next@Example.com"
    )
    reset = db.scalars(
        select(EmailToken).where(
            EmailToken.user_id == user.id, EmailToken.purpose == EmailKind.RESET_PASSWORD
        )
    ).one()
    changed = db.scalars(
        select(EmailToken).where(
            EmailToken.user_id == user.id, EmailToken.purpose == EmailKind.CHANGE_EMAIL
        )
    ).one()
    assert reset.token_hash == EmailService.hash_token(raw) and raw not in reset.token_hash
    db_now = db.execute(select(func.now())).scalar_one()  # the database clock
    assert reset.expires_at == db_now + TOKEN_TTL[EmailKind.RESET_PASSWORD]
    assert changed.new_email == "next@example.com"
    stored = db.execute(text("SELECT t::text FROM email_tokens AS t")).all()
    own_mail = text("SELECT t::text FROM email_outbox AS t WHERE t.user_id = :user_id")
    outbox: str = db.execute(own_mail, {"user_id": user.id}).scalar_one()
    assert raw not in " ".join(row[0] for row in stored) and raw not in outbox
    assert change not in outbox
    assert "commit" not in transaction_calls


def test_bad_tokens_raise_invalid_token(db: Session, make_user: Callable[..., User]) -> None:
    user = make_user()
    raw = EmailService.create_token(user, EmailKind.RESET_PASSWORD, db)
    with pytest.raises(AppError) as unknown:
        EmailService.consume_token("missing-token", EmailKind.RESET_PASSWORD, db)
    assert unknown.value.code == ErrorCode.INVALID_TOKEN
    assert unknown.value.status == HTTPStatus.BAD_REQUEST
    with pytest.raises(AppError):
        EmailService.consume_token(raw, EmailKind.VERIFY_EMAIL, db)
    row = db.scalars(select(EmailToken).where(EmailToken.user_id == user.id)).one()
    assert row.used_at is None
    row.expires_at = datetime.now(UTC) - timedelta(hours=1)
    db.flush()
    with pytest.raises(AppError) as expired:
        EmailService.consume_token(raw, EmailKind.RESET_PASSWORD, db)
    assert expired.value.message == "Verification or reset link is invalid, used or expired"
    db.refresh(row)
    assert row.used_at is None
    row.expires_at = datetime.now(UTC) + timedelta(minutes=30)
    db.flush()
    assert EmailService.consume_token(raw, EmailKind.RESET_PASSWORD, db).user_id == user.id
    with pytest.raises(AppError) as used:
        EmailService.consume_token(raw, EmailKind.RESET_PASSWORD, db)
    assert used.value.code == ErrorCode.INVALID_TOKEN


def test_is_link_live_matches_what_consume_token_accepts(
    db: Session, make_user: Callable[..., User]
) -> None:
    user = make_user()
    raw = EmailService.create_token(user, EmailKind.RESET_PASSWORD, db)
    assert EmailService.is_link_live(raw, EmailKind.RESET_PASSWORD, db)
    assert not EmailService.is_link_live("missing-token", EmailKind.RESET_PASSWORD, db)
    assert not EmailService.is_link_live(raw, EmailKind.VERIFY_EMAIL, db)  # other kind
    row = db.scalars(select(EmailToken).where(EmailToken.user_id == user.id)).one()
    row.expires_at = datetime.now(UTC) - timedelta(minutes=1)
    db.flush()
    assert not EmailService.is_link_live(raw, EmailKind.RESET_PASSWORD, db)  # expired
    row.expires_at = datetime.now(UTC) + timedelta(minutes=30)
    db.flush()
    assert EmailService.is_link_live(raw, EmailKind.RESET_PASSWORD, db)
    EmailService.consume_token(raw, EmailKind.RESET_PASSWORD, db)
    assert not EmailService.is_link_live(raw, EmailKind.RESET_PASSWORD, db)  # used


def test_change_email_and_notices(db: Session, make_user: Callable[..., User]) -> None:
    user = make_user()
    with pytest.raises(ValueError, match="new_email"):
        EmailService.create_token(user, EmailKind.CHANGE_EMAIL, db)
    with pytest.raises(ValueError, match="new_email"):
        EmailService.create_token(user, EmailKind.CHANGE_EMAIL, db, new_email=" ")
    with pytest.raises(ValueError, match="only for change_email"):
        EmailService.create_token(user, EmailKind.VERIFY_EMAIL, db, new_email="a@example.com")
    with pytest.raises(ValueError, match="not a link token"):
        EmailService.create_token(user, EmailKind.TA_INVITATION, db)
    with pytest.raises(ValueError, match="not a link token"):
        EmailService.invalidate_user_tokens(user.id, EmailKind.EMAIL_CHANGED_NOTICE, db)
    with pytest.raises(ValueError):
        EmailService.queue_email(user, "welcome", db)  # type: ignore[arg-type]
    raw = EmailService.create_token(user, EmailKind.CHANGE_EMAIL, db, new_email=" Next@x.com ")
    assert EmailService.consume_token(raw, EmailKind.CHANGE_EMAIL, db).new_email == "next@x.com"
