import pytest
from sqlalchemy import Result, Row, select
from sqlalchemy.orm import Session

from app.accounts.register import RegisterRequest, register_account
from app.errors import AppError, ErrorCode
from app.models.accounts import User


def test_valid_register(db: Session):
    request = RegisterRequest(
        email="test@email.com", username="JohnLanguage", password="ValidPassword1"
    )
    register_account(request, db)
    query = select(User.username).where(User.email == request.email)
    result: Result = db.execute(query)
    rows: Row = result.first()
    for row in rows:
        assert row.lower() == request.username.lower()


def test_username_taken(db: Session):
    request_1 = RegisterRequest(
        email="example@email.com", username="John", password="ValidPassword1"
    )
    request_2 = RegisterRequest(
        email="example2@email.com", username="John", password="ValidPassword1"
    )
    with pytest.raises(AppError) as exc_info:
        register_account(request_1, db)
        register_account(request_2, db)
    assert exc_info.value.code == ErrorCode.USERNAME_TAKEN


def test_email_taken(db: Session):
    request_1 = RegisterRequest(
        email="example@email.com", username="John1", password="ValidPassword1"
    )
    request_2 = RegisterRequest(
        email="example@email.com", username="John2", password="ValidPassword1"
    )
    with pytest.raises(AppError) as exc_info:
        register_account(request_1, db)
        register_account(request_2, db)
    assert exc_info.value.code == ErrorCode.EMAIL_TAKEN


def test_invalid_username(db: Session):
    request = RegisterRequest(email="example@email.com", username="jl", password="ValidPassword1")
    with pytest.raises(AppError) as exc_info:
        register_account(request, db)
    assert exc_info.value.code == ErrorCode.VALIDATION_ERROR


def test_invalid_email(db: Session):
    request = RegisterRequest(
        email="invalidemail", username="JohnLanguage", password="ValidPassword1"
    )
    with pytest.raises(AppError) as exc_info:
        register_account(request, db)
    assert exc_info.value.code == ErrorCode.VALIDATION_ERROR


def test_password_too_short(db: Session):
    with pytest.raises(AppError) as exc_info:
        request = RegisterRequest(
            email="example@email.com", username="JohnLanguage", password="Ab123"
        )
        register_account(request, db)
    assert exc_info.value.code == ErrorCode.VALIDATION_ERROR


def test_password_no_number(db: Session):
    with pytest.raises(AppError) as exc_info:
        request = RegisterRequest(
            email="example@email.com", username="JohnLanguage", password="Abcdefghijk"
        )
        register_account(request, db)
    assert exc_info.value.code == ErrorCode.VALIDATION_ERROR


def test_password_no_letter(db: Session):
    with pytest.raises(AppError) as exc_info:
        request = RegisterRequest(
            email="example@email.com", username="JohnLanguage", password="123456789"
        )
        register_account(request, db)
    assert exc_info.value.code == ErrorCode.VALIDATION_ERROR


def test_password_no_capital(db: Session):
    with pytest.raises(AppError) as exc_info:
        request = RegisterRequest(
            email="example@email.com", username="JohnLanguage", password="abcdefgh123456"
        )
        register_account(request, db)
    assert exc_info.value.code == ErrorCode.VALIDATION_ERROR
