from uuid import uuid4

import pytest
from fastapi import HTTPException
from sqlalchemy.orm import Session

from app.accounts.interface import (
    ClassStaffRole,
    Language,
    can_manage_content,
    get_current_user,
    is_ta_in_language,
    require_class_role,
)

_STUB_TOKEN = "abcde"
_STUB_UUID = uuid4()


def test_get_current_user_returns_user(db: Session):
    user = get_current_user(_STUB_TOKEN, db)
    assert user


def test_require_class_role_raises_exception(db: Session):
    _STUB_USER = get_current_user(_STUB_TOKEN, db)
    with pytest.raises(HTTPException) as exc_info:
        require_class_role(_STUB_USER, _STUB_UUID, db, ClassStaffRole.OWNER)
    assert exc_info.type is HTTPException
    assert exc_info.value.status_code == 403


def test_ta_is_in_language(db: Session):
    assert is_ta_in_language(_STUB_UUID, Language.ES, db)


def test_ta_is_not_in_language(db: Session):
    assert not is_ta_in_language(_STUB_UUID, Language.JA, db)

def test_can_manage_content(db: Session):
    _STUB_USER = get_current_user(_STUB_TOKEN, db)
    _STUB_USER.can_manage_content = True
    assert can_manage_content(_STUB_USER)

def test_cannot_manage_content(db: Session):
    _STUB_USER = get_current_user(_STUB_TOKEN, db)
    _STUB_USER.can_manage_content = False
    assert not can_manage_content(_STUB_USER)