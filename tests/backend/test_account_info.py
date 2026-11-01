from collections.abc import Callable

import pytest
from fastapi import Response
from fastapi.testclient import TestClient

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


def profile(client: TestClient, access_token: str) -> Response:
    response = client.get("/api/v1/me/profile", headers={"Authorization": f"Bearer {access_token}"})
    return response


def test_profile_returns_correct_info(client: TestClient, student: User):
    access_token = login(client).json()["access_token"]
    profile_response = profile(client, access_token).json()
    assert profile_response["email"] == student.email
    assert profile_response["username"] == student.username
    assert profile_response["role"] == student.role


def test_unauthorized_user_cannot_retrieve_profile(client: TestClient):
    response = client.get("/api/v1/me/profile")
    assert response.status_code == 401
    assert response.json()["error"]["code"] == ErrorCode.NOT_AUTHENTICATED
