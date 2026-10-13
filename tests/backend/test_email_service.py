"""EmailService queues mail and checks hashed link tokens. It does not send."""

import hashlib
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from http import HTTPStatus
from uuid import uuid4

import pytest
from sqlalchemy import func, select, text
from sqlalchemy.orm import Session

from app.errors import AppError, ErrorCode
from app.models.accounts import User
from app.models.email import (
    HIGH_PRIORITY_KINDS,
    LOW_PRIORITY_KINDS,
    TOKEN_KINDS,
    EmailOutbox,
    EmailToken,
    priority_for,
)
from app.models.enums import EmailKind, EmailStatus
from app.services.email import TOKEN_TTL, EmailService


def test_every_kind_has_exactly_one_priority() -> None:
    # a new EmailKind missing from both groups would fail priority_matches_template
    assert set(HIGH_PRIORITY_KINDS).isdisjoint(LOW_PRIORITY_KINDS)
    assert set(HIGH_PRIORITY_KINDS) | set(LOW_PRIORITY_KINDS) == set(EmailKind)


def test_token_helpers_and_lifetimes() -> None:
    assert set(TOKEN_TTL) == set(TOKEN_KINDS)
    assert TOKEN_TTL[EmailKind.RESET_PASSWORD] == timedelta(minutes=60)
    assert TOKEN_TTL[EmailKind.VERIFY_EMAIL] == timedelta(days=7)
    assert TOKEN_TTL[EmailKind.SET_PASSWORD] == TOKEN_TTL[EmailKind.CHANGE_EMAIL]
    raw = EmailService.generate_token()
    assert raw != EmailService.generate_token()
    assert len(raw) == 43  # secrets.token_urlsafe(32), without padding
    assert EmailService.hash_token(raw) == hashlib.sha256(raw.encode()).hexdigest()
    assert EmailService.revoke_all_sessions(uuid4()) is None


@pytest.mark.parametrize("kind", list(EmailKind))
def test_queue_email_sets_priority_and_stores_no_token(
    db: Session, make_user: Callable[..., User], kind: EmailKind
) -> None:
    user = make_user()
    row = EmailService.queue_email(db, user, kind)
    assert (row.to_email, row.template, row.priority) == (user.email, kind, priority_for(kind))
    assert row.status == EmailStatus.PENDING and row.attempts == 0 and row.params == {}
    assert row.sent_at is None
    assert db.scalar(select(func.count()).select_from(EmailToken)) == 0


def test_second_pending_reset_returns_the_queued_row(
    db: Session, make_user: Callable[..., User]
) -> None:
    user = make_user()
    first = EmailService.queue_email(db, user, EmailKind.RESET_PASSWORD)
    again = EmailService.queue_email(db, user, EmailKind.RESET_PASSWORD)
    assert again.id == first.id
    first.status, first.sent_at = EmailStatus.SENT, datetime.now(UTC)
    db.flush()
    assert EmailService.queue_email(db, user, EmailKind.RESET_PASSWORD).id != first.id
    resets = select(func.count()).where(EmailOutbox.template == EmailKind.RESET_PASSWORD)
    assert db.scalar(resets) == 2


def test_reset_sent_between_conflict_and_read_is_queued_again(
    db: Session, make_user: Callable[..., User], monkeypatch: pytest.MonkeyPatch
) -> None:
    user = make_user()
    first = EmailService.queue_email(db, user, EmailKind.RESET_PASSWORD)
    real_scalars = db.scalars
    calls = {"n": 0}

    def send_first_then_read(*args: object, **kwargs: object) -> object:
        calls["n"] += 1
        if calls["n"] == 2:  # the read after the conflict: the job sent it meanwhile
            first.status, first.sent_at = EmailStatus.SENT, datetime.now(UTC)
            db.flush()
        return real_scalars(*args, **kwargs)  # type: ignore[call-overload]

    monkeypatch.setattr(db, "scalars", send_first_then_read)
    again = EmailService.queue_email(db, user, EmailKind.RESET_PASSWORD)
    assert again.id != first.id and again.status == EmailStatus.PENDING


def test_a_pending_reset_does_not_block_other_mail(
    db: Session, make_user: Callable[..., User]
) -> None:
    user = make_user()
    reset = EmailService.queue_email(db, user, EmailKind.RESET_PASSWORD)
    verify = EmailService.queue_email(db, user, EmailKind.VERIFY_EMAIL)
    assert verify.id != reset.id
    pending = select(func.count()).where(
        EmailOutbox.user_id == user.id, EmailOutbox.status == EmailStatus.PENDING
    )
    assert db.scalar(pending) == 2


def test_a_repeated_reset_goes_to_the_current_address(
    db: Session, make_user: Callable[..., User]
) -> None:
    user = make_user()
    first = EmailService.queue_email(db, user, EmailKind.RESET_PASSWORD)
    user.email = "changed@example.com"
    db.flush()
    again = EmailService.queue_email(db, user, EmailKind.RESET_PASSWORD)
    assert again.id == first.id and again.to_email == "changed@example.com"


def test_addresses_are_trimmed_and_lowercased(db: Session, make_user: Callable[..., User]) -> None:
    user = make_user()
    raw = EmailService.create_token(db, user, EmailKind.CHANGE_EMAIL, new_email=" A@b.com ")
    assert EmailService.consume_token(db, raw, EmailKind.CHANGE_EMAIL).new_email == "a@b.com"
    # an address typed with spaces and capitals is stored and counted as the same address
    padded = f"  {user.email.upper()} "
    unsaved = User(id=user.id, email=padded)  # not added to the session, only read
    row = EmailService.queue_email(db, unsaved, EmailKind.VERIFY_EMAIL)
    assert row.to_email == user.email
    window = timedelta(hours=1)
    assert EmailService.count_recent(db, padded, EmailKind.VERIFY_EMAIL, window) == 1


def test_a_new_link_replaces_the_older_one_of_the_same_kind(
    db: Session, make_user: Callable[..., User]
) -> None:
    user = make_user()
    older = EmailService.create_token(db, user, EmailKind.RESET_PASSWORD)
    verify = EmailService.create_token(db, user, EmailKind.VERIFY_EMAIL)
    newer = EmailService.create_token(db, user, EmailKind.RESET_PASSWORD)
    with pytest.raises(AppError):
        EmailService.consume_token(db, older, EmailKind.RESET_PASSWORD)
    assert EmailService.consume_token(db, newer, EmailKind.RESET_PASSWORD).user_id == user.id
    assert EmailService.consume_token(db, verify, EmailKind.VERIFY_EMAIL).user_id == user.id


def test_count_recent_is_per_address_kind_and_window(
    db: Session, make_user: Callable[..., User]
) -> None:
    user = make_user()
    other = make_user()
    now = datetime.now(UTC)

    def at(kind: EmailKind, owner: User, age: timedelta, *, sent: bool = False) -> None:
        row = EmailService.queue_email(db, owner, kind)
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
    assert EmailService.count_recent(db, user.email.upper(), EmailKind.VERIFY_EMAIL, window) == 3
    assert EmailService.count_recent(db, other.email, EmailKind.VERIFY_EMAIL, window) == 1
    assert EmailService.count_recent(db, user.email, EmailKind.RESET_PASSWORD, window) == 1
    assert EmailService.count_recent(db, "nobody@example.com", EmailKind.VERIFY_EMAIL, window) == 0


def test_create_token_stores_only_the_hash(
    db: Session, make_user: Callable[..., User], transaction_calls: list[str]
) -> None:
    user = make_user()
    EmailService.queue_email(db, user, EmailKind.RESET_PASSWORD)
    before = datetime.now(UTC)
    raw = EmailService.create_token(db, user, EmailKind.RESET_PASSWORD)
    change = EmailService.create_token(
        db, user, EmailKind.CHANGE_EMAIL, new_email="Next@Example.com"
    )
    after = datetime.now(UTC)
    reset = db.scalars(
        select(EmailToken).where(EmailToken.purpose == EmailKind.RESET_PASSWORD)
    ).one()
    changed = db.scalars(
        select(EmailToken).where(EmailToken.purpose == EmailKind.CHANGE_EMAIL)
    ).one()
    assert reset.token_hash == EmailService.hash_token(raw) and raw not in reset.token_hash
    assert before + TOKEN_TTL[EmailKind.RESET_PASSWORD] <= reset.expires_at
    assert reset.expires_at <= after + TOKEN_TTL[EmailKind.RESET_PASSWORD]
    assert changed.new_email == "next@example.com"
    stored = db.execute(text("SELECT t::text FROM email_tokens AS t")).all()
    outbox: str = db.execute(text("SELECT t::text FROM email_outbox AS t")).scalar_one()
    assert raw not in " ".join(row[0] for row in stored) and raw not in outbox
    assert change not in outbox
    assert "commit" not in transaction_calls


def test_bad_tokens_raise_invalid_token(db: Session, make_user: Callable[..., User]) -> None:
    user = make_user()
    raw = EmailService.create_token(db, user, EmailKind.RESET_PASSWORD)
    with pytest.raises(AppError) as unknown:
        EmailService.consume_token(db, "missing-token", EmailKind.RESET_PASSWORD)
    assert unknown.value.code == ErrorCode.INVALID_TOKEN
    assert unknown.value.status == HTTPStatus.BAD_REQUEST
    with pytest.raises(AppError):
        EmailService.consume_token(db, raw, EmailKind.VERIFY_EMAIL)
    row = db.scalars(select(EmailToken)).one()
    assert row.used_at is None
    row.expires_at = datetime.now(UTC) - timedelta(hours=1)
    db.flush()
    with pytest.raises(AppError) as expired:
        EmailService.consume_token(db, raw, EmailKind.RESET_PASSWORD)
    assert expired.value.message == "Verification or reset link is invalid, used or expired"
    db.refresh(row)
    assert row.used_at is None
    row.expires_at = datetime.now(UTC) + timedelta(minutes=30)
    db.flush()
    assert EmailService.consume_token(db, raw, EmailKind.RESET_PASSWORD).user_id == user.id
    with pytest.raises(AppError) as used:
        EmailService.consume_token(db, raw, EmailKind.RESET_PASSWORD)
    assert used.value.code == ErrorCode.INVALID_TOKEN


def test_change_email_and_notices(db: Session, make_user: Callable[..., User]) -> None:
    user = make_user()
    with pytest.raises(ValueError, match="new_email"):
        EmailService.create_token(db, user, EmailKind.CHANGE_EMAIL)
    with pytest.raises(ValueError, match="new_email"):
        EmailService.create_token(db, user, EmailKind.CHANGE_EMAIL, new_email=" ")
    with pytest.raises(ValueError, match="only for change_email"):
        EmailService.create_token(db, user, EmailKind.VERIFY_EMAIL, new_email="a@example.com")
    with pytest.raises(ValueError, match="not a link token"):
        EmailService.create_token(db, user, EmailKind.TA_INVITATION)
    with pytest.raises(ValueError, match="not a link token"):
        EmailService.invalidate_user_tokens(db, user.id, EmailKind.EMAIL_CHANGED_NOTICE)
    with pytest.raises(ValueError):
        EmailService.queue_email(db, user, "welcome")  # type: ignore[arg-type]
    raw = EmailService.create_token(db, user, EmailKind.CHANGE_EMAIL, new_email="next@example.com")
    assert (
        EmailService.consume_token(db, raw, EmailKind.CHANGE_EMAIL).new_email == "next@example.com"
    )


def test_invalidate_user_tokens_drops_only_unused_links_of_that_kind(
    db: Session, make_user: Callable[..., User]
) -> None:
    user = make_user()
    other = make_user()
    reset_raw = EmailService.create_token(db, user, EmailKind.RESET_PASSWORD)
    old_raw = EmailService.create_token(db, user, EmailKind.VERIFY_EMAIL)
    other_raw = EmailService.create_token(db, other, EmailKind.VERIFY_EMAIL)
    used = EmailService.consume_token(
        db, EmailService.create_token(db, user, EmailKind.VERIFY_EMAIL), EmailKind.VERIFY_EMAIL
    )
    used.used_at = datetime.now(UTC) - timedelta(days=1)
    db.flush()
    kept = used.used_at
    EmailService.invalidate_user_tokens(db, user.id, EmailKind.VERIFY_EMAIL)
    with pytest.raises(AppError):
        EmailService.consume_token(db, old_raw, EmailKind.VERIFY_EMAIL)
    EmailService.consume_token(db, reset_raw, EmailKind.RESET_PASSWORD)
    EmailService.consume_token(db, other_raw, EmailKind.VERIFY_EMAIL)
    db.refresh(used)
    assert used.used_at == kept
