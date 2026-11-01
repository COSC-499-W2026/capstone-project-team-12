from collections.abc import Callable

import pytest
from fastapi import Response
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.accounts.password import hash_password
from app.errors import ErrorCode
from app.models.accounts import User

VALID_EMAIL = "test@email.com"
VALID_PASSWORD = "ValidPassword1"


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


def logout(client: TestClient, access_token: str = None) -> Response:
    response = client.post(
        "/api/v1/auth/logout", headers={"Authorization": f"Bearer {access_token}"}
    )
    return response


def test_logout_revokes_tokens(client: TestClient, student: User, db: Session):
    tokens = login(client)
    access_token = tokens.json()["access_token"]
    refresh_token = tokens.cookies["refresh_token"]
    refresh_response = refresh(client, refresh_token)  # Access restricted route and assert 200
    new_refresh_token = refresh_response.cookies["refresh_token"]
    assert refresh_response.status_code == 204
    logout(client, access_token)
    denied_refresh = refresh(client, new_refresh_token)
    assert denied_refresh.status_code == 401
    assert denied_refresh.json()["error"]["code"] == ErrorCode.NOT_AUTHENTICATED


def test_logout_without_being_signed_in_returns_401(client: TestClient):
    response = logout(client)
    assert response.status_code == 401
    assert response.json()["error"]["code"] == ErrorCode.NOT_AUTHENTICATED
