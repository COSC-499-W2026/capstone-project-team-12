"""Reset-password through the API."""

from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from http import HTTPStatus

import pytest
from bcrypt import checkpw
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.accounts.password import hash_password
from app.errors import AppError
from app.models.accounts import User
from app.models.email import EmailToken
from app.models.enums import EmailKind, EmailStatus
from app.services.email import EmailService

URL = "/api/v1/auth/reset-password"
NEW = "NewPassword1"


def test_a_reset_link_sets_the_new_password_once(
    client: TestClient, db: Session, make_user: Callable[..., User]
) -> None:
    old = "OldPassword1"
    user = make_user(email_verified_at=datetime.now(UTC), password_hash=hash_password(old))
    token = EmailService.create_token(user, EmailKind.RESET_PASSWORD, db)
    waiting = EmailService.queue_email(user, EmailKind.RESET_PASSWORD, db)  # e.g. paused
    others = [
        EmailService.queue_email(make_user(), EmailKind.RESET_PASSWORD, db),
        EmailService.queue_email(user, EmailKind.VERIFY_EMAIL, db),
    ]
    setup = EmailService.create_token(user, EmailKind.SET_PASSWORD, db)
    response = client.post(URL, json={"token": token, "new_password": NEW})
    assert (response.status_code, response.json()) == (HTTPStatus.OK, {"status": "ok"})
    db.refresh(user)
    assert user.password_hash is not None
    assert checkpw(NEW.encode(), user.password_hash.encode())
    assert not checkpw(old.encode(), user.password_hash.encode())
    db.refresh(waiting)
    assert waiting.status == EmailStatus.FAILED  # won't send another link
    for row in others:  # only this user's reset mail
        db.refresh(row)
        assert row.status == EmailStatus.PENDING
    with pytest.raises(AppError):  # it can't overwrite the new password
        EmailService.consume_token(setup, EmailKind.SET_PASSWORD, db)
    again = client.post(URL, json={"token": token, "new_password": "Another1pass"})
    assert again.status_code == HTTPStatus.BAD_REQUEST
    assert again.json()["error"]["code"] == "INVALID_TOKEN"


# too short, over bcrypt's 70 bytes, over the request's 200 characters
@pytest.mark.parametrize("bad", ["short", "Aa1" + "x" * 68, "Aa1" + "x" * 200])
def test_a_rejected_password_doesnt_use_the_link_up(
    client: TestClient, db: Session, make_user: Callable[..., User], bad: str
) -> None:
    user = make_user(email_verified_at=datetime.now(UTC))
    token = EmailService.create_token(user, EmailKind.RESET_PASSWORD, db)
    weak = client.post(URL, json={"token": token, "new_password": bad})
    assert (weak.status_code, weak.json()["error"]["code"]) == (
        HTTPStatus.UNPROCESSABLE_ENTITY,
        "VALIDATION_ERROR",
    )
    assert client.post(URL, json={"token": token, "new_password": NEW}).status_code == HTTPStatus.OK


@pytest.mark.parametrize("case", ["missing", "wrong_kind", "expired", "used"])
def test_a_dead_link_is_refused_before_the_password_is_hashed(
    client: TestClient,
    db: Session,
    make_user: Callable[..., User],
    monkeypatch: pytest.MonkeyPatch,
    case: str,
) -> None:
    def no_hash(_password: str) -> str:
        raise AssertionError("bcrypt ran for a link that can't work")

    monkeypatch.setattr("app.accounts.email_links.hash_password", no_hash)
    user = make_user(email_verified_at=datetime.now(UTC))
    kind = EmailKind.VERIFY_EMAIL if case == "wrong_kind" else EmailKind.RESET_PASSWORD
    token = EmailService.create_token(user, kind, db)
    link = db.scalars(select(EmailToken).where(EmailToken.user_id == user.id)).one()
    if case == "expired":
        link.expires_at = datetime.now(UTC) - timedelta(minutes=1)
    if case == "used":
        link.used_at = datetime.now(UTC)
    db.flush()
    sent = "no-such-token" if case == "missing" else token
    response = client.post(URL, json={"token": sent, "new_password": NEW})
    assert response.status_code == HTTPStatus.BAD_REQUEST
    assert response.json()["error"]["code"] == "INVALID_TOKEN"


@pytest.mark.parametrize("case", ["unverified", "inactive", "verify_link", "expired", "missing"])
def test_a_link_that_cant_reset_is_invalid_and_stays_unused(
    client: TestClient, db: Session, make_user: Callable[..., User], case: str
) -> None:
    verified = None if case == "unverified" else datetime.now(UTC)
    user = make_user(email_verified_at=verified, is_active=case != "inactive")
    kind = EmailKind.VERIFY_EMAIL if case == "verify_link" else EmailKind.RESET_PASSWORD
    token = EmailService.create_token(user, kind, db)
    link = db.scalars(select(EmailToken).where(EmailToken.user_id == user.id)).one()
    if case == "expired":
        link.expires_at = datetime.now(UTC) - timedelta(minutes=1)
        db.flush()
    sent = "no-such-token" if case == "missing" else token
    response = client.post(URL, json={"token": sent, "new_password": NEW})
    assert response.status_code == HTTPStatus.BAD_REQUEST
    assert response.json()["error"]["code"] == "INVALID_TOKEN"
    db.refresh(user)
    db.refresh(link)
    assert (user.password_hash, link.used_at) == (None, None)
