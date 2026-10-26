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
    return client.post("/api/v1/auth/login", data={"username": email, "password": password})


def refresh(client: TestClient, refresh_token: str) -> Response:
    # a header, not the client's cookie jar, so no cookie leaks into the next request
    return client.post("/api/v1/auth/refresh", headers={"Cookie": f"refresh_token={refresh_token}"})


def test_valid_login(client: TestClient):
    register = {"email": VALID_EMAIL, "username": VALID_USERNAME, "password": VALID_PASSWORD}
    client.post("api/v1/auth/register", json=register)
    login = {"username": VALID_EMAIL, "password": VALID_PASSWORD}
    response = client.post("api/v1/auth/login", data=login)
    tokens = response.json()
    assert tokens["access_token"]
    assert tokens["refresh_token"]
    assert response.status_code == 200

    cookies = client.cookies
    cookies.set("refresh_token", tokens["refresh_token"])
    client.cookies = cookies

    sleep(1)  # Must sleep briefly so the timestamp on JWT is different

    client.headers = {"Authorization": f"Bearer {tokens['access_token']}"}
    new_tokens = client.post("api/v1/auth/refresh")
    refresh_json = new_tokens.json()
    assert refresh_json["access_token"] != tokens["access_token"]
    assert new_tokens.status_code == 200


def test_invalid_email(client: TestClient, student: User):
    response = login(client, email=INVALID_EMAIL)
    assert response.status_code == 401
    assert response.json()["error"]["code"] == ErrorCode.INVALID_CREDENTIALS


def test_invalid_password(client: TestClient, student: User):
    response = login(client, password=INVALID_PASSWORD)
    assert response.status_code == 401
    assert response.json()["error"]["code"] == ErrorCode.INVALID_CREDENTIALS


def test_revoke_tokens(client: TestClient, db: Session, student: User):
    refresh_token = login(client).json()["refresh_token"]

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
    refresh_token = login(client).json()["refresh_token"]
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
    refresh_token = login(client).json()["refresh_token"]
    student.is_active = False
    db.flush()
    response = refresh(client, refresh_token)
    assert response.status_code == 403
    assert response.json()["error"]["code"] == ErrorCode.ACCOUNT_DISABLED
