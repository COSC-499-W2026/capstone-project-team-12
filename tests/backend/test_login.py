import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.accounts.interface import revoke_all_refresh_tokens
from app.accounts.login import LoginRequest, login_account
from app.accounts.register import RegisterRequest, register_account
from app.errors import AppError, ErrorCode
from app.models.accounts import RefreshToken, User

VALID_EMAIL = "test@email.com"
VALID_USERNAME = "JohnLanguage"
VALID_PASSWORD = "ValidPassword1"

INVALID_EMAIL = "invalidemail"
INVALID_PASSWORD = "NotValidPassword1"


def test_valid_login(db: Session, client: TestClient):
    register = {
        "email": VALID_EMAIL,
        "username": VALID_USERNAME,
        "password": VALID_PASSWORD
    }
    client.post("/auth/register", json=register)
    login = {
        "username": VALID_EMAIL,
        "password": VALID_PASSWORD
    }
    response = client.post("/auth/login", json=login)
    assert response
    assert response.status_code == 200
    assert response.json.get("access_token")
    assert response.json.get("refresh_token")


def test_invalid_email(db: Session):
    register_request = RegisterRequest(
        email=VALID_EMAIL, username=VALID_USERNAME, password=VALID_PASSWORD
    )
    register_account(register_request, db)
    with pytest.raises(AppError) as exc_info:
        login_request = LoginRequest(email=INVALID_EMAIL, password=VALID_PASSWORD)
        login_account(login_request, db)
    assert exc_info.value.code == ErrorCode.INVALID_CREDENTIALS


def test_invalid_password(db: Session):
    register_request = RegisterRequest(
        email=VALID_EMAIL, username=VALID_USERNAME, password=VALID_PASSWORD
    )
    register_account(register_request, db)
    with pytest.raises(AppError) as exc_info:
        login_request = LoginRequest(
            email=VALID_EMAIL, username=VALID_USERNAME, password=INVALID_PASSWORD
        )
        login_account(login_request, db)
    assert exc_info.value.code == ErrorCode.INVALID_CREDENTIALS


def test_revoke_tokens(db: Session):

    register_request = RegisterRequest(
        email=VALID_EMAIL, username=VALID_USERNAME, password=VALID_PASSWORD
    )

    register_account(register_request, db)
    login_request = LoginRequest(email=VALID_EMAIL, password=VALID_PASSWORD)

    tokens = login_account(login_request, db)
    assert tokens.get("refresh_token")

    stmt = select(User.id).where(User.email == VALID_EMAIL)
    result = db.execute(stmt)
    if result:
        for row in result:
            user_id = row[0]

    revoke_all_refresh_tokens(user_id, db)
    token_select = select(RefreshToken.revoked_at).where(RefreshToken.user_id == user_id)
    revoked_result = db.execute(token_select)
    for row in revoked_result:
        assert row[0] is not None
