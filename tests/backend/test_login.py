from time import sleep

from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.accounts.interface import revoke_all_refresh_tokens
from app.accounts.register import RegisterRequest, register_account
from app.errors import ErrorCode
from app.models.accounts import RefreshToken, User

VALID_EMAIL = "test@email.com"
VALID_USERNAME = "JohnLanguage"
VALID_PASSWORD = "ValidPassword1"

INVALID_EMAIL = "invalidemail"
INVALID_PASSWORD = "NotValidPassword1"


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


def test_invalid_email(client: TestClient, db: Session):
    register_request = RegisterRequest(
        email=VALID_EMAIL, username=VALID_USERNAME, password=VALID_PASSWORD
    )
    register_account(register_request, db)
    login = {"username": INVALID_EMAIL, "password": VALID_PASSWORD}
    response = client.post("/api/v1/auth/login", data=login)
    assert response.json()["error"]["code"] == ErrorCode.INVALID_CREDENTIALS


def test_invalid_password(client: TestClient, db: Session):
    register_request = RegisterRequest(
        email=VALID_EMAIL, username=VALID_USERNAME, password=VALID_PASSWORD
    )
    register_account(register_request, db)
    login = {"username": VALID_EMAIL, "password": INVALID_PASSWORD}
    response = client.post("/api/v1/auth/login", data=login)
    assert response.json()["error"]["code"] == ErrorCode.INVALID_CREDENTIALS
