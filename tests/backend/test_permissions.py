import pytest
from sqlalchemy.orm import Session

from app.accounts.interface import (
    ClassStaffRole,
    can_manage_content,
    get_current_user,
    is_class_staff,
    require_class_role,
)
from app.errors import AppError, ErrorCode

_STUB_TOKEN = "abcde"


def test_get_current_user_invalid_token(db: Session):
    with pytest.raises(AppError) as exc_info:
        get_current_user("", db)  #  Invalid token
    assert exc_info.value.code == ErrorCode.NOT_AUTHENTICATED


def test_require_class_role_owner(make_user, make_class_staff, make_class, db: Session):
    user = make_user()
    fake_class = make_class()
    make_class_staff(fake_class, user)
    require_class_role(user, fake_class.id, ClassStaffRole.OWNER, db)
    assert True  #  Code cannot reach this unless successful


def test_require_class_role_assistant(make_user, make_class_staff, make_class, db: Session):
    user = make_user()
    fake_class = make_class()
    make_class_staff(fake_class, user, role=ClassStaffRole.ASSISTANT)
    require_class_role(user, fake_class.id, ClassStaffRole.ASSISTANT, db)
    assert True  #  Code cannot reach this unless successful


def test_require_class_role_raises_exception(make_user, make_class, make_class_staff, db: Session):
    user = make_user()
    fake_class = make_class()
    make_class_staff(fake_class, user, role=ClassStaffRole.ASSISTANT)
    with pytest.raises(AppError) as exc_info:
        require_class_role(user, fake_class.id, ClassStaffRole.OWNER, db)
    assert exc_info.value.code == ErrorCode.FORBIDDEN_ROLE


def test_ta_is_ta_in_class(make_user, make_class_staff, make_class, db: Session):
    user = make_user()
    test_class = make_class()
    make_class_staff(test_class, user, role=ClassStaffRole.ASSISTANT)

    assert is_class_staff(user.id, test_class, db)


def test_ta_is_not_in_class(make_user, make_class, db: Session):
    user = make_user()
    test_class = make_class()
    assert not is_class_staff(user.id, test_class, db)


def test_can_manage_content(make_user, db: Session):
    user = make_user()
    user.can_manage_content = True
    assert can_manage_content(user)


def test_cannot_manage_content(make_user, db: Session):
    user = make_user()
    assert not can_manage_content(user)
