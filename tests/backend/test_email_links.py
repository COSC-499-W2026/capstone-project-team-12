"""Verify-email, resend-verification and forgot-password through the API."""

from collections.abc import Callable
from datetime import UTC, datetime
from http import HTTPStatus

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models.accounts import User
from app.models.email import EmailOutbox
from app.models.enums import EmailKind
from app.services.email import EmailService

API = "/api/v1/auth"


def queued(db: Session, kind: EmailKind) -> int:
    query = select(func.count()).select_from(EmailOutbox).where(EmailOutbox.template == kind)
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
    user = make_user()
    response = client.post(f"{API}/{path}", json={"email": f"  {user.email.upper()} "})
    assert (response.status_code, response.json()) == (HTTPStatus.ACCEPTED, {"status": "ok"})
    row = db.scalars(select(EmailOutbox).where(EmailOutbox.template == kind)).one()
    assert (row.user_id, row.to_email) == (user.id, user.email)


@pytest.mark.parametrize(
    ("path", "account"),
    [
        ("resend-verification", "none"),
        ("resend-verification", "inactive"),
        ("resend-verification", "verified"),
        ("forgot-password", "none"),
        ("forgot-password", "inactive"),
    ],
)
def test_requests_for_no_usable_account_look_the_same_and_queue_nothing(
    client: TestClient, db: Session, make_user: Callable[..., User], path: str, account: str
) -> None:
    email = "nobody@example.com"
    if account == "inactive":
        email = make_user(is_active=False).email
    elif account == "verified":
        email = make_user(email_verified_at=datetime.now(UTC)).email
    response = client.post(f"{API}/{path}", json={"email": email})
    assert (response.status_code, response.json()) == (HTTPStatus.ACCEPTED, {"status": "ok"})
    assert db.scalar(select(func.count()).select_from(EmailOutbox)) == 0


def test_more_than_three_requests_an_hour_queue_nothing_more(
    client: TestClient, db: Session, make_user: Callable[..., User]
) -> None:
    user = make_user()
    for _ in range(5):
        response = client.post(f"{API}/resend-verification", json={"email": user.email})
        # no 429: it would show that the account exists
        assert response.status_code == HTTPStatus.ACCEPTED
    assert queued(db, EmailKind.VERIFY_EMAIL) == 3


def test_a_second_forgot_password_while_one_is_pending_adds_nothing(
    client: TestClient, db: Session, make_user: Callable[..., User]
) -> None:
    user = make_user()
    for _ in range(2):
        client.post(f"{API}/forgot-password", json={"email": user.email})
    assert queued(db, EmailKind.RESET_PASSWORD) == 1


@pytest.mark.parametrize(
    ("path", "body"),
    [("verify-email", {}), ("verify-email", {"token": ""}), ("forgot-password", {"email": ""})],
)
def test_missing_fields_are_a_validation_error(
    client: TestClient, path: str, body: dict[str, str]
) -> None:
    response = client.post(f"{API}/{path}", json=body)
    assert response.status_code == HTTPStatus.UNPROCESSABLE_ENTITY
    assert response.json()["error"]["code"] == "VALIDATION_ERROR"
