from uuid import uuid4

import pytest
from sqlalchemy.orm import Session

from app.accounts.interface import (
    STUB_OWNER_ID,
    ClassStaffRole,
    ForbiddenRoleError,
    Language,
    UserNotFoundError,
    can_manage_content,
    get_current_user,
    is_ta_in_language,
    require_class_role,
)

_STUB_TOKEN = "abcde"
_STUB_CLASS = uuid4()


def test_get_current_user_valid_token(db: Session):
    user = get_current_user(_STUB_TOKEN, db)
    assert user  #  Code cannot reach this statement unless successful


def test_get_current_user_invalid_token(db: Session):
    with pytest.raises(UserNotFoundError) as exc_info:
        get_current_user("", db)  #  Invalid token
    assert exc_info.type is UserNotFoundError


def test_require_class_role_pass(db: Session):
    _STUB_USER = get_current_user(_STUB_TOKEN, db)
    _STUB_USER.id = STUB_OWNER_ID
    require_class_role(_STUB_USER, _STUB_CLASS, db, ClassStaffRole.OWNER)
    assert True  #  Code cannot reach this statement unless successful


def test_require_class_role_raises_exception(db: Session):
    _STUB_USER = get_current_user(_STUB_TOKEN, db)
    with pytest.raises(ForbiddenRoleError) as exc_info:
        require_class_role(_STUB_USER, _STUB_CLASS, db, ClassStaffRole.OWNER)
    assert exc_info.type is ForbiddenRoleError


def test_ta_is_in_language(db: Session):
    assert is_ta_in_language(_STUB_CLASS, Language.ES, db)


def test_ta_is_not_in_language(db: Session):
    assert not is_ta_in_language(_STUB_CLASS, Language.JA, db)


def test_can_manage_content(db: Session):
    _STUB_USER = get_current_user(_STUB_TOKEN, db)
    _STUB_USER.can_manage_content = True
    assert can_manage_content(_STUB_USER)


def test_cannot_manage_content(db: Session):
    _STUB_USER = get_current_user(_STUB_TOKEN, db)
    _STUB_USER.can_manage_content = False
    assert not can_manage_content(_STUB_USER)
