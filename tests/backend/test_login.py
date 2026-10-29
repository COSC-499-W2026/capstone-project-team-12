from collections.abc import Callable
from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient
from httpx2 import Response
from jwt import decode
from sqlalchemy import select, update
from sqlalchemy.orm import Session

from app.accounts.interface import revoke_all_refresh_tokens
from app.accounts.password import hash_password
from app.config import get_settings
from app.errors import ErrorCode
from app.models.accounts import RefreshToken, User

VALID_EMAIL = "test@email.com"
VALID_PASSWORD = "ValidPassword1"

INVALID_EMAIL = "invalidemail"
INVALID_PASSWORD = "NotValidPassword1"


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


def test_login_returns_working_tokens(client: TestClient, student: User):
    response = login(client)
    assert response.status_code == 200
    body = response.json()
    assert body["token_type"] == "bearer"
    assert response.cookies["refresh_token"]
    # the access token must work on a protected route: a bad access code gives 400, not 401
    joined = client.post(
        "/api/v1/me/classes/join",
        json={"access_code": "abc"},
        headers={"Authorization": f"Bearer {body['access_token']}"},
    )
    assert joined.status_code == 400
    assert joined.json()["error"]["code"] == ErrorCode.INVALID_ACCESS_CODE


def test_access_token_expires_in_60_minutes(client: TestClient, student: User):
    access_token = login(client).json()["access_token"]
    key = str(get_settings().access_token_secret_key)
    claims = decode(access_token, key, ["HS256"])
    expected = datetime.now(UTC) + timedelta(minutes=60)
    assert abs(claims["exp"] - expected.timestamp()) < 60


def test_refresh_rotates_tokens(client: TestClient, student: User):
    old_refresh_token = login(client).cookies["refresh_token"]
    response = refresh(client, old_refresh_token)
    assert response.status_code == 200
    assert response.json()["access_token"]
    assert response.cookies["refresh_token"] != old_refresh_token


def test_rotated_refresh_token_is_rejected(client: TestClient, student: User):
    old_refresh_token = login(client).cookies["refresh_token"]
    assert refresh(client, old_refresh_token).status_code == 200
    reused = refresh(client, old_refresh_token)
    assert reused.status_code == 401
    assert reused.json()["error"]["code"] == ErrorCode.NOT_AUTHENTICATED


def test_invalid_email(client: TestClient, student: User):
    response = login(client, email=INVALID_EMAIL)
    assert response.status_code == 401
    assert response.json()["error"]["code"] == ErrorCode.INVALID_CREDENTIALS


def test_invalid_password(client: TestClient, student: User):
    response = login(client, password=INVALID_PASSWORD)
    assert response.status_code == 401
    assert response.json()["error"]["code"] == ErrorCode.INVALID_CREDENTIALS


def test_revoke_tokens(client: TestClient, db: Session, student: User):
    refresh_token = login(client).cookies["refresh_token"]

    revoke_all_refresh_tokens(student.id, db)

    revoked = db.scalars(
        select(RefreshToken.revoked_at).where(RefreshToken.user_id == student.id)
    ).all()
    assert revoked  # the login really made a token
    assert all(revoked_at is not None for revoked_at in revoked)
    assert refresh(client, refresh_token).status_code == 401


def test_refresh_with_no_refresh_token(client: TestClient):
    response = client.post("/api/v1/auth/refresh")
    assert response.status_code == 401
    assert response.json()["error"]["code"] == ErrorCode.NOT_AUTHENTICATED


def test_expired_refresh_token_is_rejected(client: TestClient, db: Session, student: User):
    refresh_token = login(client).cookies["refresh_token"]
    db.execute(
        update(RefreshToken)
        .where(RefreshToken.user_id == student.id)
        .values(expires_at=datetime.now(UTC) - timedelta(seconds=1))
    )
    assert refresh(client, refresh_token).status_code == 401


def test_disabled_account_cannot_login(client: TestClient, db: Session, student: User):
    student.is_active = False
    db.flush()
    response = login(client)
    assert response.status_code == 403
    assert response.json()["error"]["code"] == ErrorCode.ACCOUNT_DISABLED


def test_disabled_account_cannot_refresh(client: TestClient, db: Session, student: User):
    refresh_token = login(client).cookies["refresh_token"]
    student.is_active = False
    db.flush()
    response = refresh(client, refresh_token)
    assert response.status_code == 403
    assert response.json()["error"]["code"] == ErrorCode.ACCOUNT_DISABLED


def test_refresh_cookie_is_protected(client: TestClient, student: User):
    cookie = login(client).headers["set-cookie"].lower()
    assert "httponly" in cookie
    assert "samesite=strict" in cookie
    assert f"max-age={14 * 24 * 60 * 60}" in cookie


def test_using_replaced_token_revokes_family(client: TestClient, student: User):
    first_token = login(client).cookies["refresh_token"]
    second_token = refresh(client, first_token).cookies["refresh_token"]
    # First should refuses because it was replaced, second should refuse because it was revoked
    assert refresh(client, first_token).json()["error"]["code"] == ErrorCode.NOT_AUTHENTICATED
    assert refresh(client, second_token).json()["error"]["code"] == ErrorCode.NOT_AUTHENTICATED
