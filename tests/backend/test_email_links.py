"""Verify-email, resend-verification and forgot-password through the API."""

import threading
from collections.abc import Callable
from datetime import UTC, datetime
from http import HTTPStatus
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import Engine, delete, func, select, text, update
from sqlalchemy.orm import Session

from app.accounts.email_links import request_verification_email
from app.models.accounts import User
from app.models.email import EmailOutbox
from app.models.enums import EmailKind, EmailStatus, UserRole
from app.services.email import EmailService

API = "/api/v1/auth"


def queued(db: Session, email: str, kind: EmailKind | None = None) -> int:
    """Outbox rows for this address only, so rows already in a dev database don't count."""
    query = select(func.count()).where(EmailOutbox.to_email == email)
    if kind is not None:
        query = query.where(EmailOutbox.template == kind)
    return db.execute(query).scalar_one()


def test_a_verify_link_confirms_the_address_once(
    client: TestClient, db: Session, make_user: Callable[..., User]
) -> None:
    user = make_user()
    token = EmailService.create_token(user, EmailKind.VERIFY_EMAIL, db)
    response = client.post(f"{API}/verify-email", json={"token": token})
    assert (response.status_code, response.json()) == (HTTPStatus.OK, {"status": "ok"})
    db.refresh(user)
    assert user.email_verified_at is not None
    again = client.post(f"{API}/verify-email", json={"token": token})
    assert again.status_code == HTTPStatus.BAD_REQUEST
    assert again.json()["error"]["code"] == "INVALID_TOKEN"


@pytest.mark.parametrize("account", ["verified", "inactive"])
def test_a_verify_link_for_a_verified_or_inactive_account(
    client: TestClient, db: Session, make_user: Callable[..., User], account: str
) -> None:
    # verified: the first time is kept. inactive: still verified, the address is confirmed
    # either way and a deactivated account can't sign in until it is reactivated
    earlier = datetime(2026, 9, 1, tzinfo=UTC)
    user = make_user(email_verified_at=earlier if account == "verified" else None)
    user.is_active = account != "inactive"
    token = EmailService.create_token(user, EmailKind.VERIFY_EMAIL, db)
    response = client.post(f"{API}/verify-email", json={"token": token})
    assert response.status_code == HTTPStatus.OK
    db.refresh(user)
    if account == "verified":
        assert user.email_verified_at == earlier
    else:
        assert user.email_verified_at is not None


def test_a_reset_token_does_not_verify(
    client: TestClient, db: Session, make_user: Callable[..., User]
) -> None:
    user = make_user()
    token = EmailService.create_token(user, EmailKind.RESET_PASSWORD, db)
    response = client.post(f"{API}/verify-email", json={"token": token})
    assert response.json()["error"]["code"] == "INVALID_TOKEN"
    db.refresh(user)
    assert user.email_verified_at is None


@pytest.mark.parametrize(
    ("path", "kind"),
    [
        ("resend-verification", EmailKind.VERIFY_EMAIL),
        ("forgot-password", EmailKind.RESET_PASSWORD),
    ],
)
def test_requests_queue_mail_for_an_account(
    client: TestClient,
    db: Session,
    make_user: Callable[..., User],
    path: str,
    kind: EmailKind,
) -> None:
    # resend is for an unverified address, reset only for a verified one
    user = make_user(email_verified_at=datetime.now(UTC) if path == "forgot-password" else None)
    response = client.post(f"{API}/{path}", json={"email": f"  {user.email.upper()} "})
    assert (response.status_code, response.json()) == (HTTPStatus.ACCEPTED, {"status": "ok"})
    query = select(EmailOutbox).where(EmailOutbox.user_id == user.id, EmailOutbox.template == kind)
    row = db.scalars(query).one()
    assert (row.user_id, row.to_email) == (user.id, user.email)


@pytest.mark.parametrize(
    ("path", "account"),
    [
        ("resend-verification", "none"),
        ("resend-verification", "inactive"),
        ("resend-verification", "verified"),
        ("forgot-password", "none"),
        ("forgot-password", "inactive"),
        ("forgot-password", "unverified"),
    ],
)
def test_requests_for_no_usable_account_look_the_same_and_queue_nothing(
    client: TestClient, db: Session, make_user: Callable[..., User], path: str, account: str
) -> None:
    email = "nobody@example.com"
    # otherwise usable for that route, so only the named reason stops it
    usable = datetime.now(UTC) if path == "forgot-password" else None
    if account == "inactive":
        email = make_user(is_active=False, email_verified_at=usable).email
    elif account == "verified":
        email = make_user(email_verified_at=datetime.now(UTC)).email
    elif account == "unverified":
        email = make_user().email
    response = client.post(f"{API}/{path}", json={"email": email})
    assert (response.status_code, response.json()) == (HTTPStatus.ACCEPTED, {"status": "ok"})
    assert queued(db, email) == 0


def test_more_than_three_requests_an_hour_queue_nothing_more(
    client: TestClient, db: Session, make_user: Callable[..., User]
) -> None:
    user = make_user()
    for _ in range(5):
        response = client.post(f"{API}/resend-verification", json={"email": user.email})
        # no 429: it would show that the account exists
        assert response.status_code == HTTPStatus.ACCEPTED
    assert queued(db, user.email, EmailKind.VERIFY_EMAIL) == 3


def test_forgot_password_also_stops_at_three_an_hour(
    client: TestClient, db: Session, make_user: Callable[..., User]
) -> None:
    user = make_user(email_verified_at=datetime.now(UTC))
    for _ in range(5):
        response = client.post(f"{API}/forgot-password", json={"email": user.email})
        assert response.status_code == HTTPStatus.ACCEPTED
        # mark it sent, or the next request would only find the pending reset
        sent = update(EmailOutbox).where(EmailOutbox.user_id == user.id)
        db.execute(sent.values(status=EmailStatus.SENT, sent_at=func.now()))
    assert queued(db, user.email, EmailKind.RESET_PASSWORD) == 3


def test_requests_sent_at_once_still_queue_only_three(test_engine: Engine) -> None:
    """Five resend requests racing, each in its own committed transaction (the savepoint
    session the other tests use can't run two at once)."""
    with Session(test_engine, expire_on_commit=False) as setup:
        user = User(
            email=f"{uuid4().hex[:12]}@example.com",
            username=f"user_{uuid4().hex[:8]}",
            role=UserRole.STUDENT,
        )
        setup.add(user)
        setup.commit()
    start = threading.Barrier(5, timeout=10)
    errors: list[BaseException] = []  # a thread's exception is otherwise only printed

    def resend() -> None:
        try:
            with Session(test_engine) as session:
                start.wait()
                request_verification_email(user.email, session)
                session.commit()
        except BaseException as error:
            errors.append(error)

    try:
        threads = [threading.Thread(target=resend) for _ in range(5)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=10)
        assert not any(thread.is_alive() for thread in threads), "a request is stuck"
        if errors:
            raise errors[0]
        with Session(test_engine) as check:
            rows = select(func.count()).where(EmailOutbox.user_id == user.id)
            assert check.scalar(rows) == 3
    finally:
        with Session(test_engine) as cleanup:
            cleanup.execute(text("SET lock_timeout = '10s'"))  # a stuck thread can't hang pytest
            cleanup.execute(delete(EmailOutbox).where(EmailOutbox.user_id == user.id))
            cleanup.execute(delete(User).where(User.id == user.id))
            cleanup.commit()


def test_a_second_forgot_password_while_one_is_pending_adds_nothing(
    client: TestClient, db: Session, make_user: Callable[..., User]
) -> None:
    user = make_user(email_verified_at=datetime.now(UTC))
    for _ in range(2):
        response = client.post(f"{API}/forgot-password", json={"email": user.email})
        assert response.status_code == HTTPStatus.ACCEPTED
    assert queued(db, user.email, EmailKind.RESET_PASSWORD) == 1


@pytest.mark.parametrize(
    ("path", "body"),
    [
        ("verify-email", {}),
        ("verify-email", {"token": ""}),
        ("forgot-password", {"email": ""}),
        ("forgot-password", {"email": "abc"}),
        ("resend-verification", {"email": "a@b"}),
    ],
)
def test_missing_or_malformed_fields_are_a_validation_error(
    client: TestClient, path: str, body: dict[str, str]
) -> None:
    response = client.post(f"{API}/{path}", json=body)
    assert response.status_code == HTTPStatus.UNPROCESSABLE_ENTITY
    assert response.json()["error"]["code"] == "VALIDATION_ERROR"
