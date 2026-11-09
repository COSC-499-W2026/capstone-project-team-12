"""Tests for POST /api/v1/me/password."""

from collections.abc import Callable
from http import HTTPStatus
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.accounts.password import hash_password
from app.errors import ErrorCode
from app.models.accounts import User
from app.models.enums import EmailKind
from app.services.email import EmailService

URL = "/api/v1/me/password"
OLD = "OldPassword1"
NEW = "NewPassword1"


@pytest.fixture
def user(make_user: Callable[..., User]) -> User:
    """An active student who signs in with OLD."""
    return make_user(password_hash=hash_password(OLD))


@pytest.fixture
def change(
    client: TestClient, auth_headers: Callable[[User], dict[str, str]]
) -> Callable[..., Any]:
    """POST /me/password as the user. The response's cookie is cleared from the client."""

    def post(user: User, current: str = OLD, new: str = NEW) -> Any:
        body = {"current_password": current, "new_password": new}
        response = client.post(URL, json=body, headers=auth_headers(user))
        client.cookies.clear()
        return response

    return post


def login(client: TestClient, user: User, password: str) -> Any:
    form = {"username": user.email, "password": password}
    response = client.post("/api/v1/auth/login", data=form)
    client.cookies.clear()
    return response


def refresh(client: TestClient, refresh_token: str) -> Any:
    headers = {"Cookie": f"refresh_token={refresh_token}"}
    response = client.post("/api/v1/auth/refresh", headers=headers)
    client.cookies.clear()
    return response


def assert_error(response: Any, status: HTTPStatus, code: ErrorCode) -> None:
    assert response.status_code == status, response.text
    assert response.json()["error"]["code"] == code.value


def assert_nothing_changed(client: TestClient, user: User, refresh_token: str) -> None:
    """The password still signs in and the session from before the request still works."""
    assert login(client, user, OLD).status_code == HTTPStatus.OK
    assert refresh(client, refresh_token).status_code == HTTPStatus.OK


def test_the_new_password_replaces_the_old_one(
    client: TestClient, change: Callable[..., Any], user: User
) -> None:
    response = change(user)

    assert (response.status_code, response.json()) == (HTTPStatus.OK, {"status": "ok"})
    assert login(client, user, NEW).status_code == HTTPStatus.OK
    assert_error(login(client, user, OLD), HTTPStatus.UNAUTHORIZED, ErrorCode.INVALID_CREDENTIALS)


def test_other_sessions_end_and_this_one_gets_a_new_refresh_token(
    client: TestClient, change: Callable[..., Any], user: User
) -> None:
    other_device = login(client, user, OLD).cookies["refresh_token"]

    new_cookie = change(user).cookies["refresh_token"]

    assert refresh(client, other_device).status_code == HTTPStatus.UNAUTHORIZED
    assert refresh(client, new_cookie).status_code == HTTPStatus.OK


def test_links_sent_before_the_change_stop_working(
    db: Session, change: Callable[..., Any], make_user: Callable[..., User], user: User
) -> None:
    other = make_user()
    kinds = (EmailKind.RESET_PASSWORD, EmailKind.SET_PASSWORD)
    links = {kind: EmailService.create_token(user, kind, db) for kind in kinds}
    others = {kind: EmailService.create_token(other, kind, db) for kind in kinds}
    verify = EmailService.create_token(user, EmailKind.VERIFY_EMAIL, db)

    assert change(user).status_code == HTTPStatus.OK

    for kind in kinds:
        assert not EmailService.is_link_live(links[kind], kind, db)
        assert EmailService.is_link_live(others[kind], kind, db)
    assert EmailService.is_link_live(verify, EmailKind.VERIFY_EMAIL, db)


def test_a_wrong_current_password_changes_nothing(
    client: TestClient, db: Session, change: Callable[..., Any], user: User
) -> None:
    session = login(client, user, OLD).cookies["refresh_token"]

    response = change(user, current="WrongPassword1")

    assert_error(response, HTTPStatus.UNAUTHORIZED, ErrorCode.INVALID_CREDENTIALS)
    assert_nothing_changed(client, user, session)


def test_the_same_wrong_password_twice_is_a_wrong_password(
    change: Callable[..., Any], user: User
) -> None:
    response = change(user, current=NEW, new=NEW)

    assert_error(response, HTTPStatus.UNAUTHORIZED, ErrorCode.INVALID_CREDENTIALS)


def test_a_password_check_that_finds_no_account_is_refused(
    client: TestClient,
    db: Session,
    change: Callable[..., Any],
    monkeypatch: pytest.MonkeyPatch,
    user: User,
) -> None:
    session = login(client, user, OLD).cookies["refresh_token"]
    monkeypatch.setattr("app.api.v1.me.check_password_hash", lambda *_: False)

    response = change(user)

    assert_error(response, HTTPStatus.UNAUTHORIZED, ErrorCode.INVALID_CREDENTIALS)
    monkeypatch.undo()
    assert_nothing_changed(client, user, session)


def test_the_current_password_cant_be_the_new_one(
    client: TestClient, db: Session, change: Callable[..., Any], user: User
) -> None:
    session = login(client, user, OLD).cookies["refresh_token"]

    response = change(user, new=OLD)

    assert_error(response, HTTPStatus.UNPROCESSABLE_ENTITY, ErrorCode.VALIDATION_ERROR)
    assert_nothing_changed(client, user, session)


# too short, over bcrypt's 70 bytes, over the request's 200 characters
@pytest.mark.parametrize("bad", ["short", "Aa1" + "x" * 68, "Aa1" + "x" * 200])
def test_a_new_password_that_breaks_the_rules_changes_nothing(
    client: TestClient, db: Session, change: Callable[..., Any], user: User, bad: str
) -> None:
    session = login(client, user, OLD).cookies["refresh_token"]

    response = change(user, new=bad)

    assert_error(response, HTTPStatus.UNPROCESSABLE_ENTITY, ErrorCode.VALIDATION_ERROR)
    assert_nothing_changed(client, user, session)


# bcrypt raises on more than 72 bytes; the second is only 40 characters
@pytest.mark.parametrize("too_long", ["x" * 73, "é" * 40])
def test_a_current_password_bcrypt_cant_take_is_a_validation_error(
    change: Callable[..., Any], user: User, too_long: str
) -> None:
    response = change(user, current=too_long)

    assert_error(response, HTTPStatus.UNPROCESSABLE_ENTITY, ErrorCode.VALIDATION_ERROR)


@pytest.mark.parametrize("field", ["current_password", "new_password"])
def test_both_passwords_are_required(
    client: TestClient,
    auth_headers: Callable[[User], dict[str, str]],
    user: User,
    field: str,
) -> None:
    body = {"current_password": OLD, "new_password": NEW}
    del body[field]

    response = client.post(URL, json=body, headers=auth_headers(user))

    assert_error(response, HTTPStatus.UNPROCESSABLE_ENTITY, ErrorCode.VALIDATION_ERROR)


def test_it_needs_an_access_token(client: TestClient) -> None:
    response = client.post(URL, json={"current_password": OLD, "new_password": NEW})

    assert_error(response, HTTPStatus.UNAUTHORIZED, ErrorCode.NOT_AUTHENTICATED)


def test_a_disabled_account_cant_change_its_password(
    change: Callable[..., Any], make_user: Callable[..., User]
) -> None:
    disabled = make_user(password_hash=hash_password(OLD), is_active=False)

    assert_error(change(disabled), HTTPStatus.FORBIDDEN, ErrorCode.ACCOUNT_DISABLED)
