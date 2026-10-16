"""email_tokens and email_outbox reject bad data at the database level."""

import hashlib
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.models.accounts import User
from app.models.email import (
    ONE_PENDING_RESET_INDEX,
    PENDING_DUE_INDEX,
    RECENT_INDEX,
    EmailOutbox,
    EmailToken,
    priority_for,
)
from app.models.enums import EmailKind, EmailStatus


def _hash(seed: str | None = None) -> str:
    return hashlib.sha256((seed or uuid4().hex).encode()).hexdigest()


def add_outbox(db: Session, user: User, **fields: object) -> None:
    template = fields.get("template", EmailKind.VERIFY_EMAIL)
    values: dict[str, object] = {
        "user_id": user.id,
        "to_email": user.email,
        "template": template,
        "priority": priority_for(EmailKind(str(template))) if template in set(EmailKind) else 1,
    }
    db.add(EmailOutbox(**(values | fields)))
    db.flush()


def add_token(db: Session, user: User, **fields: object) -> EmailToken:
    values: dict[str, object] = {
        "user_id": user.id,
        "purpose": EmailKind.VERIFY_EMAIL,
        "token_hash": _hash(),
        "expires_at": datetime.now(UTC) + timedelta(days=1),
    }
    db.add(row := EmailToken(**(values | fields)))
    db.flush()
    return row


@pytest.mark.parametrize(
    ("name", "expected"),
    [
        (PENDING_DUE_INDEX, ["(priority, created_at)", "pending"]),
        (RECENT_INDEX, ["(to_email, template, created_at)"]),
        (ONE_PENDING_RESET_INDEX, ["UNIQUE", "(user_id)", "reset_password", "pending"]),
    ],
)
def test_indexes_exist(db: Session, name: str, expected: list[str]) -> None:
    definition: str = db.execute(
        text("SELECT indexdef FROM pg_indexes WHERE indexname = :name"), {"name": name}
    ).scalar_one()
    assert all(part in definition for part in expected)


@pytest.mark.parametrize("kind", list(EmailKind))
def test_each_kind_stores_its_priority(
    db: Session, make_user: Callable[..., User], kind: EmailKind
) -> None:
    add_outbox(db, make_user(), template=kind)


@pytest.mark.parametrize(
    ("fields", "constraint"),
    [
        ({"template": "welcome"}, "ck_email_outbox_template"),
        ({"status": "queued"}, "ck_email_outbox_status"),
        ({"attempts": -1}, "ck_email_outbox_attempts"),
        ({"attempts": 6}, "ck_email_outbox_attempts"),
        ({"status": EmailStatus.SENT}, "ck_email_outbox_sent_at_matches_status"),
        ({"sent_at": datetime.now(UTC)}, "ck_email_outbox_sent_at_matches_status"),
        ({"to_email": "Mia@Example.com"}, "ck_email_outbox_to_email_lowercase"),
        ({"params": ["nope"]}, "ck_email_outbox_params_object"),
        ({"params": {"token": "raw"}}, "ck_email_outbox_params_have_no_token"),
        ({"params": {"token_hash": "x"}}, "ck_email_outbox_params_have_no_token"),
        ({"priority": 1}, "ck_email_outbox_priority_matches_template"),
        ({"priority": 0}, "ck_email_outbox_priority_matches_template"),
        ({"priority": 3}, "ck_email_outbox_priority_matches_template"),
        ({"to_email": None}, "not-null"),
        (
            {"template": EmailKind.RESET_PASSWORD, "priority": 2},
            "ck_email_outbox_priority_matches_template",
        ),
    ],
)
def test_outbox_constraints(
    db: Session, make_user: Callable[..., User], fields: dict[str, object], constraint: str
) -> None:
    with pytest.raises(IntegrityError, match=constraint):
        add_outbox(db, make_user(), **fields)


def test_sent_and_failed_rows_are_allowed(db: Session, make_user: Callable[..., User]) -> None:
    add_outbox(db, make_user(), status=EmailStatus.SENT, sent_at=datetime.now(UTC))
    add_outbox(
        db,
        make_user(),
        status=EmailStatus.FAILED,
        attempts=5,
        params={"new_email": "next@example.com"},
    )


@pytest.mark.parametrize(
    ("fields", "constraint"),
    [
        ({"token_hash": "not-a-hash"}, "ck_email_tokens_token_hash_sha256"),
        ({"token_hash": "AB" * 32}, "ck_email_tokens_token_hash_sha256"),
        ({"purpose": EmailKind.EMAIL_CHANGED_NOTICE}, "ck_email_tokens_purpose"),
        ({"purpose": EmailKind.TA_INVITATION}, "ck_email_tokens_purpose"),
        ({"token_hash": None}, "not-null"),
        ({"expires_at": None}, "not-null"),
        ({"new_email": "next@example.com"}, "ck_email_tokens_new_email_matches_purpose"),
        ({"purpose": EmailKind.CHANGE_EMAIL}, "ck_email_tokens_new_email_matches_purpose"),
        (
            {"purpose": EmailKind.CHANGE_EMAIL, "new_email": "Next@Example.com"},
            "ck_email_tokens_new_email_lowercase",
        ),
    ],
)
def test_token_constraints(
    db: Session, make_user: Callable[..., User], fields: dict[str, object], constraint: str
) -> None:
    with pytest.raises(IntegrityError, match=constraint):
        add_token(db, make_user(), **fields)


@pytest.mark.parametrize(
    ("kind", "new_email"),
    [
        (EmailKind.VERIFY_EMAIL, None),
        (EmailKind.RESET_PASSWORD, None),
        (EmailKind.SET_PASSWORD, None),
        (EmailKind.CHANGE_EMAIL, "next@example.com"),
    ],
)
def test_each_token_kind_is_stored(
    db: Session, make_user: Callable[..., User], kind: EmailKind, new_email: str | None
) -> None:
    assert add_token(db, make_user(), purpose=kind, new_email=new_email).used_at is None


def test_token_hash_is_unique(db: Session, make_user: Callable[..., User]) -> None:
    user = make_user()
    add_token(db, user, token_hash=_hash("same"))
    with pytest.raises(IntegrityError, match="uq_email_tokens_token_hash"):
        add_token(db, user, token_hash=_hash("same"))


def test_only_one_pending_reset_per_user(db: Session, make_user: Callable[..., User]) -> None:
    user = make_user()
    reset = EmailKind.RESET_PASSWORD
    add_outbox(db, user, template=reset, status=EmailStatus.SENT, sent_at=datetime.now(UTC))
    add_outbox(db, user, template=reset)
    add_outbox(db, user)  # a pending verify email is unaffected
    add_outbox(db, make_user(), template=reset)  # another user's reset is unaffected
    with pytest.raises(IntegrityError, match=ONE_PENDING_RESET_INDEX):
        add_outbox(db, user, template=reset)


def test_user_must_exist(db: Session) -> None:
    with pytest.raises(IntegrityError, match="fk_email_outbox_user_id_users"):
        add_outbox(db, User(id=uuid4(), email="missing@example.com"))
    db.rollback()
    with pytest.raises(IntegrityError, match="fk_email_tokens_user_id_users"):
        add_token(db, User(id=uuid4(), email="missing@example.com"))
