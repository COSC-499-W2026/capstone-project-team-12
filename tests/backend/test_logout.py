from collections.abc import Callable

import pytest
from fastapi.testclient import TestClient
from httpx2 import Response
from sqlalchemy.orm import Session

from app.accounts.password import hash_password
from app.errors import ErrorCode
from app.models.accounts import User

VALID_EMAIL = "test@email.com"
VALID_PASSWORD = "ValidPassword1"
OTHER_EMAIL = "other@email.com"


@pytest.fixture
def student(make_user: Callable[..., User]) -> User:
    """An active student who can sign in with VALID_EMAIL and VALID_PASSWORD."""
    return make_user(email=VALID_EMAIL, password_hash=hash_password(VALID_PASSWORD))


def login(client: TestClient, email: str = VALID_EMAIL, password: str = VALID_PASSWORD) -> Response:
    response = client.post("/api/v1/auth/login", data={"username": email, "password": password})
    client.cookies.clear()  # tests send the refresh cookie themselves, so none leaks between calls
    return response


def refresh(client: TestClient, refresh_token: str) -> Response:
    response = client.post(
        "/api/v1/auth/refresh", headers={"Cookie": f"refresh_token={refresh_token}"}
    )
    client.cookies.clear()
    return response


def logout(client: TestClient, access_token: str | None = None) -> Response:
    """Log out with the access token, or with no Authorization header when none is given."""
    headers = {"Authorization": f"Bearer {access_token}"} if access_token else None
    response = client.post("/api/v1/auth/logout", headers=headers)
    client.cookies.clear()
    return response


def test_logout_revokes_tokens(client: TestClient, student: User):
    tokens = login(client)
    access_token = tokens.json()["access_token"]
    refresh_response = refresh(client, tokens.cookies["refresh_token"])
    assert refresh_response.status_code == 200
    new_refresh_token = refresh_response.cookies["refresh_token"]

    assert logout(client, access_token).status_code == 204

    denied_refresh = refresh(client, new_refresh_token)
    assert denied_refresh.status_code == 401
    assert denied_refresh.json()["error"]["code"] == ErrorCode.NOT_AUTHENTICATED


def test_logout_clears_refresh_cookie(client: TestClient, student: User):
    access_token = login(client).json()["access_token"]
    set_cookie = logout(client, access_token).headers["set-cookie"]
    assert 'refresh_token=""' in set_cookie
    assert "Max-Age=0" in set_cookie


def test_logout_revokes_every_session(client: TestClient, student: User):
    first_session = login(client)
    second_session = login(client)

    assert logout(client, first_session.json()["access_token"]).status_code == 204

    assert refresh(client, first_session.cookies["refresh_token"]).status_code == 401
    assert refresh(client, second_session.cookies["refresh_token"]).status_code == 401


def test_logout_keeps_other_users_signed_in(
    client: TestClient, student: User, make_user: Callable[..., User]
):
    make_user(email=OTHER_EMAIL, password_hash=hash_password(VALID_PASSWORD))
    other_refresh_token = login(client, email=OTHER_EMAIL).cookies["refresh_token"]
    access_token = login(client).json()["access_token"]

    assert logout(client, access_token).status_code == 204

    assert refresh(client, other_refresh_token).status_code == 200


def test_login_works_after_logout(client: TestClient, student: User):
    assert logout(client, login(client).json()["access_token"]).status_code == 204

    refresh_token = login(client).cookies["refresh_token"]

    assert refresh(client, refresh_token).status_code == 200


def test_logout_without_being_signed_in_returns_401(client: TestClient):
    response = logout(client)
    assert response.status_code == 401
    assert response.json()["error"]["code"] == ErrorCode.NOT_AUTHENTICATED


def test_logout_with_invalid_access_token_returns_401(client: TestClient):
    response = logout(client, "not-a-real-token")
    assert response.status_code == 401
    assert response.json()["error"]["code"] == ErrorCode.NOT_AUTHENTICATED


def test_disabled_account_cannot_logout(client: TestClient, db: Session, student: User):
    access_token = login(client).json()["access_token"]
    student.is_active = False
    db.flush()
    response = logout(client, access_token)
    assert response.status_code == 403
    assert response.json()["error"]["code"] == ErrorCode.ACCOUNT_DISABLED
