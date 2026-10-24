from uuid import uuid4

import pytest
from bcrypt import checkpw
from fastapi.testclient import TestClient
from sqlalchemy import Result, Row, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.accounts.register import RegisterRequest, register_account
from app.errors import AppError, ErrorCode
from app.models.accounts import User
from app.models.email import EmailOutbox
from app.models.engagement import AccessCode, Class, ClassEnrollment
from app.models.enums import EmailKind
from app.services.email import EmailService


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


def test_sign_up_queues_a_verification_email(db: Session) -> None:
    request = RegisterRequest(email="new@email.com", username="NewStudent", password="Valid1pass")
    register_account(request, db)
    row = db.scalars(select(EmailOutbox).where(EmailOutbox.to_email == request.email)).one()
    assert row.template == EmailKind.VERIFY_EMAIL


def test_an_unexpected_integrity_error_is_raised(
    db: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    def fail(*args: object, **kwargs: object) -> None:
        raise IntegrityError("INSERT INTO email_outbox", {}, Exception("another constraint"))

    monkeypatch.setattr(EmailService, "queue_email", fail)
    name = uuid4().hex[:12]
    request = RegisterRequest(email=f"{name}@example.com", username=name, password="Valid1pass")
    with pytest.raises(IntegrityError):
        register_account(request, db)


def test_valid_hash(db: Session):
    request = RegisterRequest(
        email="test@email.com", username="JohnLanguage", password="ValidPassword1"
    )
    register_account(request, db)
    query = select(User.password_hash).where(User.email == request.email)
    result = db.execute(query)
    assert result
    for row in result:
        stored_password = str(row[0])
        stored_bytes = stored_password.encode("utf-8")
        input_bytes = request.password.encode("utf-8")
        assert checkpw(input_bytes, stored_bytes)


def test_201_with_code(
    client: TestClient, make_class: Class, make_access_code: AccessCode, db: Session
):
    request = {
        "email": "test@email.com",
        "username": "JohnLanguage",
        "password": "ValidPassword1",
        "access_code": "ABCDEFGH",
    }
    test_class = make_class()
    make_access_code(school_class=test_class, code="ABCDEFGH")
    response_201 = client.post("/api/v1/auth/register", json=request)
    assert response_201.status_code == 201

    query = select(User).where(User.email == "test@email.com")

    user = db.execute(query).scalar_one_or_none()
    assert user is not None
    assert user.email == request["email"]
    assert user.username == request["username"]
    class_query = select(ClassEnrollment).where(ClassEnrollment.student_id == user.id)
    result = db.execute(class_query).scalar_one_or_none()
    assert user.current_class_id == result.class_id


def test_201_no_code(client: TestClient, db: Session):
    request = {
        "email": "test@email.com",
        "username": "JohnLanguage",
        "password": "ValidPassword1",
    }
    response_201 = client.post("/api/v1/auth/register", json=request)
    assert response_201.status_code == 201

    query = select(User).where(User.email == "test@email.com")

    user = db.execute(query).scalar_one_or_none()
    assert user is not None
    assert user.email == request["email"]
    assert user.username == request["username"]


def test_invalid_access_code(client: TestClient, db: Session):
    request = {
        "email": "invalid@code.com",
        "username": "JohnLanguage",
        "password": "ValidPassword1",
        "access_code": "abc",
    }
    response = client.post("/api/v1/auth/register", json=request)
    assert response.status_code == 400
    assert response.json()["error"]["code"] == ErrorCode.INVALID_ACCESS_CODE
    user = db.scalars(select(User).where(User.email == request["email"])).one()
    assert user is None
    enrollment = db.execute(
        select(ClassEnrollment).where(ClassEnrollment.student_id == user.id)
    ).scalar_one_or_none()
    assert enrollment is None


def test_409(client: TestClient):
    request = {
        "email": "test@email.com",
        "username": "JohnLanguage",
        "password": "ValidPassword1",
    }
    client.post("/api/v1/auth/register", json=request)
    response_409 = client.post("/api/v1/auth/register", json=request)
    assert response_409.status_code == 409


def test_422(client: TestClient):
    request = {
        "email": "bademail",
        "username": "JohnLanguage",
        "password": "ValidPassword1",
    }
    response_422 = client.post("/api/v1/auth/register", json=request)
    assert response_422.status_code == 422


def test_username_taken(db: Session, client: TestClient):
    request_1 = {
        "email": "test@email.com",
        "username": "JohnLanguage",
        "password": "ValidPassword1",
    }
    request_2 = {
        "email": "test@email.com",
        "username": "johnLanguage",
        "password": "ValidPassword1",
    }
    result1 = client.post("/api/v1/auth/register", json=request_1)
    result2 = client.post("/api/v1/auth/register", json=request_2)
    assert (
        result1.status_code == 201
        and result2.status_code == 409
        and result2.json()["error"]["code"] == "USERNAME_TAKEN"
    )


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
