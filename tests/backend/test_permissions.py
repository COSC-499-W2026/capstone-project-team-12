from uuid import uuid4

import pytest
from sqlalchemy.orm import Session

from app.accounts.interface import (
    Language,
    can_manage_content,
    get_current_user,
    is_ta_in_language,
    require_class_role,
)


@pytest.fixture
def user_roles():
    _stub_id = uuid4()
    return [_stub_id]


def test_get_current_user_returns_user(db: Session):
    _stub_token = "abcde"
    user = get_current_user(_stub_token, db)
    assert user


def test_class_owner_can_access_owner_requirement(db: Session):
    _stub_token = "abcde"
    result = require_class_role(_stub_token, db, "owner")
    assert result


def test_assistant_can_access_assistant_requirement(db: Session):
    _stub_token = "abcde"
    result = require_class_role(_stub_token, db, "assistant")
    assert result


def test_ta_in_language_returns_true(db: Session):
    result = is_ta_in_language(user_roles, Language.ES, db)
    assert result


def test_user_can_manage_content(db: Session):
    result = can_manage_content(user_roles, db)
    assert result
