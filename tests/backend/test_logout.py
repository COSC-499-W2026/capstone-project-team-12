import hashlib
from collections.abc import Callable
from datetime import datetime, timedelta

import pytest
from fastapi.testclient import TestClient
from httpx2 import Response
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.accounts.logout import logout_session
from app.accounts.password import hash_password
from app.accounts.tokens import encode_jwt
from app.config import get_settings
from app.errors import ErrorCode
from app.models.accounts import RefreshToken, User

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


def logout(
    client: TestClient, refresh_token: str | None = None, access_token: str | None = None
) -> Response:
    """Log out with the refresh cookie and access token, sending each only when given."""
    headers = {}
    if refresh_token is not None:
        headers["Cookie"] = f"refresh_token={refresh_token}"
    if access_token is not None:
        headers["Authorization"] = f"Bearer {access_token}"
    response = client.post("/api/v1/auth/logout", headers=headers)
    client.cookies.clear()
    return response


def revoked_at(db: Session, refresh_token: str) -> datetime | None:
    token_hash = hashlib.sha256(refresh_token.encode()).hexdigest()
    return db.scalar(select(RefreshToken.revoked_at).where(RefreshToken.token_hash == token_hash))


def test_logout_revokes_tokens(client: TestClient, student: User):
    refresh_response = refresh(client, login(client).cookies["refresh_token"])
    assert refresh_response.status_code == 200
    new_refresh_token = refresh_response.cookies["refresh_token"]

    assert logout(client, new_refresh_token).status_code == 204

    denied_refresh = refresh(client, new_refresh_token)
    assert denied_refresh.status_code == 401
    assert denied_refresh.json()["error"]["code"] == ErrorCode.NOT_AUTHENTICATED


def test_logout_with_expired_access_token_revokes_session(client: TestClient, student: User):
    refresh_token = login(client).cookies["refresh_token"]
    key = str(get_settings().access_token_secret_key)
    expired_access_token = encode_jwt({"sub": str(student.id)}, timedelta(minutes=-2), key)

    assert logout(client, refresh_token, expired_access_token).status_code == 204

    denied_refresh = refresh(client, refresh_token)
    assert denied_refresh.status_code == 401
    assert denied_refresh.json()["error"]["code"] == ErrorCode.NOT_AUTHENTICATED


def test_logout_keeps_other_sign_in_working(client: TestClient, student: User):
    first_sign_in = login(client).cookies["refresh_token"]
    second_sign_in = login(client).cookies["refresh_token"]

    assert logout(client, first_sign_in).status_code == 204

    assert refresh(client, first_sign_in).status_code == 401
    assert refresh(client, second_sign_in).status_code == 200


def test_logout_clears_refresh_cookie(client: TestClient, student: User):
    # keep the cookie in the client's jar: the jar only drops it if the delete matches its path
    client.post("/api/v1/auth/login", data={"username": VALID_EMAIL, "password": VALID_PASSWORD})
    refresh_token = client.cookies["refresh_token"]

    response = client.post(
        "/api/v1/auth/logout", headers={"Cookie": f"refresh_token={refresh_token}"}
    )

    assert response.status_code == 204
    assert "refresh_token" not in client.cookies


def test_repeated_logout_keeps_revoked_at(client: TestClient, db: Session, student: User):
    refresh_token = login(client).cookies["refresh_token"]
    assert logout(client, refresh_token).status_code == 204
    first_revoked_at = revoked_at(db, refresh_token)
    assert first_revoked_at is not None

    assert logout(client, refresh_token).status_code == 204

    db.expire_all()  # read the row again, not the cached value
    assert revoked_at(db, refresh_token) == first_revoked_at


def test_logout_session_does_not_commit(
    client: TestClient, db: Session, student: User, transaction_calls: list[str]
):
    refresh_token = login(client).cookies["refresh_token"]
    transaction_calls.clear()

    logout_session(refresh_token, db)

    assert "commit" not in transaction_calls
    assert revoked_at(db, refresh_token) is not None


def test_logout_keeps_other_users_signed_in(
    client: TestClient, student: User, make_user: Callable[..., User]
):
    make_user(email=OTHER_EMAIL, password_hash=hash_password(VALID_PASSWORD))
    other_refresh_token = login(client, email=OTHER_EMAIL).cookies["refresh_token"]
    refresh_token = login(client).cookies["refresh_token"]

    assert logout(client, refresh_token).status_code == 204

    assert refresh(client, other_refresh_token).status_code == 200


def test_login_works_after_logout(client: TestClient, student: User):
    assert logout(client, login(client).cookies["refresh_token"]).status_code == 204

    refresh_token = login(client).cookies["refresh_token"]

    assert refresh(client, refresh_token).status_code == 200


def test_logout_without_refresh_cookie_returns_204(client: TestClient):
    assert logout(client).status_code == 204


def test_logout_with_unknown_refresh_token_returns_204(client: TestClient):
    assert logout(client, "not-a-real-token").status_code == 204


def test_disabled_account_can_logout(client: TestClient, db: Session, student: User):
    refresh_token = login(client).cookies["refresh_token"]
    student.is_active = False
    db.flush()

    assert logout(client, refresh_token).status_code == 204

    assert revoked_at(db, refresh_token) is not None
