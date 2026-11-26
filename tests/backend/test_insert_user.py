"""insert_user: the account rules shared by sign-up and admin-created staff accounts."""

from collections.abc import Callable
from typing import Any
from uuid import uuid4

import pytest
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.accounts import register
from app.accounts.register import insert_user
from app.errors import AppError, ErrorCode
from app.models.accounts import User
from app.models.enums import UserRole


def new_account() -> dict[str, Any]:
    """An address and a username no account has."""
    return {"email": f"{uuid4().hex[:12]}@example.com", "username": f"u_{uuid4().hex[:8]}"}


def insert(db: Session, **fields: Any) -> User:
    values = new_account() | {"password": None} | fields
    return insert_user(
        values["email"],
        values["username"],
        values["password"],
        role=UserRole.STUDENT,
        can_manage_content=False,
        db=db,
    )


def user_count(db: Session) -> int:
    return db.scalar(select(func.count()).select_from(User)) or 0


@pytest.mark.parametrize(
    ("taken", "code"),
    [("email", ErrorCode.EMAIL_TAKEN), ("username", ErrorCode.USERNAME_TAKEN)],
)
def test_an_email_or_username_in_use_in_any_case_is_409_and_nothing_is_added(
    db: Session, make_user: Callable[..., User], taken: str, code: ErrorCode
) -> None:
    existing = make_user(is_active=False)  # a deactivated account still holds both
    users_before = user_count(db)

    with pytest.raises(AppError) as raised:
        insert(db, **{taken: getattr(existing, taken).upper()})

    assert raised.value.code == code
    assert user_count(db) == users_before


@pytest.mark.parametrize(
    ("check", "taken", "code"),
    [
        ("email_exists", "email", ErrorCode.EMAIL_TAKEN),
        ("username_exists", "username", ErrorCode.USERNAME_TAKEN),
    ],
)
def test_an_email_or_username_taken_after_the_check_is_still_409(
    db: Session,
    make_user: Callable[..., User],
    monkeypatch: pytest.MonkeyPatch,
    check: str,
    taken: str,
    code: ErrorCode,
) -> None:
    """Another request takes the address or username between the check and the insert: the
    unique index refuses the insert, and that is the same 409 as the check gives."""
    existing = make_user()
    monkeypatch.setattr(register, check, lambda *_: False)

    with pytest.raises(AppError) as raised:
        insert(db, **{taken: getattr(existing, taken).upper()})

    assert raised.value.code == code


@pytest.mark.parametrize(
    ("fields", "field"),
    [
        ({"email": "not-an-email"}, "email"),
        ({"username": "ab"}, "username"),
        ({"username": "x" * 31}, "username"),
        ({"username": " padded "}, "username"),
    ],
)
def test_a_badly_formed_field_is_422_with_its_detail(
    db: Session, fields: dict[str, str], field: str
) -> None:
    with pytest.raises(AppError) as raised:
        insert(db, **fields)

    assert raised.value.code == ErrorCode.VALIDATION_ERROR
    assert [(detail["in"], detail["field"]) for detail in raised.value.details] == [("body", field)]
