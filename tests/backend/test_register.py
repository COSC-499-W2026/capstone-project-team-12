import pytest

from sqlalchemy.orm import Session
from sqlalchemy import select, query, where, Result, Row

from app.accounts.interface import student_register, RegisterRequest
from app.errors import AppError, ErrorCode
from app.models.accounts import User

def test_valid_register(db: Session):
    request = RegisterRequest("example@email.com", "JohnLanguage", "ValidPassword1")
    try:
        student_register(request.email, request.username, request.password)
    except:
        assert False
    else:
        try:
            query = db.select(User.username).where
            result: Result = db.execute(query).first()
            assert result is Row
        except:
            assert False

def test_username_taken(db: Session):
    request_1 = RegisterRequest("example@email.com", "John", "ValidPassword1")
    request_2 = RegisterRequest("example2@email.com", "John", "ValidPassword1")
    with pytest.raises(AppError) as exc_info:
        student_register(request_1.email, request_1.username, request_1.password)
        student_register(request_2.email, request_2.username, request_2.password)
    assert exc_info.value.code == ErrorCode.USERNAME_TAKEN


def test_email_taken(db: Session):
    request_1 = RegisterRequest("example@email.com", "John1", "ValidPassword1")
    request_2 = RegisterRequest("example@email.com", "John2", "ValidPassword1")
    with pytest.raises(AppError) as exc_info:
        student_register(request_1.email, request_1.username, request_1.password)
        student_register(request_2.email, request_2.username, request_2.password)
    assert exc_info.value.code == ErrorCode.EMAIL_TAKEN


def test_invalid_email(db: Session):
    request = RegisterRequest("invalidemail", "JohnLanguage", "ValidPassword1")
    with pytest.raises(AppError) as exc_info:
        student_register(request.email, request.username, request.password)
    assert exc_info.value.code == ErrorCode.INVALID_CREDENTIALS


def test_invalid_password(db: Session):
    with pytest.raises(AppError) as exc_info:
        request = RegisterRequest("example@email.com", "JohnLanguage", "123")
        student_register(request.email, request.username, request.password)
    assert exc_info.value.code == ErrorCode.INVALID_CREDENTIALS