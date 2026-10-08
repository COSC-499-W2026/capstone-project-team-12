from uuid import uuid4

import pytest
from sqlalchemy.orm import Session

from fastapi import HTTPException

from app.models.accounts import User

from app.accounts.interface import (
    Language,
    ClassStaffRole,
    can_manage_content,
    get_current_user,
    is_ta_in_language,
    require_class_role,
)


_STUB_USER = User()
_STUB_TOKEN = "abcde"
_STUB_UUID = uuid4()

def test_get_current_user_returns_user(db: Session):
    user = get_current_user(_STUB_TOKEN, db)
    assert user


def test_require_class_role_raises_exception(db: Session):
    _STUB_USER = get_current_user(_STUB_TOKEN)
    with pytest.raises(HTTPException) as exc_info:
        require_class_role(_STUB_USER, _STUB_UUID, db, ClassStaffRole.OWNER)
    assert exc_info.type is HTTPException
    assert exc_info.detail == "Unauthorized access"


def test_ta_is_in_language(db: Session):
    assert is_ta_in_language(_STUB_UUID, Language.ES, db)


def test_ta_is_not_in_language(db: Session):
    assert not is_ta_in_language(_STUB_UUID, Language.JA, db)


def test_user_can_manage_content():
    assert can_manage_content(_STUB_USER)


def test_user_cannot_manage_content():
    assert not can_manage_content(_STUB_USER)
