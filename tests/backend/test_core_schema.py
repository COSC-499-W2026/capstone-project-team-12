"""The core tables (#8) reject bad data at the database level, not only in the services."""

from typing import Any
from uuid import uuid4

import pytest
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.db import Base
from app.models.accounts import ClassStaff, User
from app.models.engagement import Class
from app.models.enums import ClassStaffRole, Language, UserRole


def add_user(db: Session, **fields: Any) -> User:
    values: dict[str, Any] = {
        "email": f"{uuid4().hex[:12]}@example.com",
        "username": f"user_{uuid4().hex[:8]}",
        "role": UserRole.STUDENT,
    }
    values.update(fields)
    user = User(**values)
    db.add(user)
    db.flush()
    return user


def add_class(db: Session, **fields: Any) -> Class:
    values: dict[str, Any] = {
        "name": "JAPN 101 - Fall 2026",
        "semester": "2026W1",
        "target_language": Language.JA,
    }
    values.update(fields)
    school_class = Class(**values)
    db.add(school_class)
    db.flush()
    return school_class


def add_staff(db: Session, school_class: Class, user: User, role: str) -> None:
    db.add(ClassStaff(class_id=school_class.id, user_id=user.id, role=role))
    db.flush()


def test_constraints_follow_the_naming_convention() -> None:
    users = Base.metadata.tables["users"]
    names = {constraint.name for constraint in users.constraints}
    assert {"pk_users", "uq_users_email", "ck_users_role", "ck_users_email_lowercase"} <= names


def test_a_user_gets_server_defaults(db: Session) -> None:
    user = add_user(db)
    db.refresh(user)
    assert user.id is not None
    assert user.is_active is True
    assert user.can_manage_content is False
    assert user.password_hash is None
    assert user.created_at.tzinfo is not None


def test_uppercase_email_is_rejected(db: Session) -> None:
    with pytest.raises(IntegrityError, match="ck_users_email_lowercase"):
        add_user(db, email="Mia@Example.com")


def test_duplicate_email_is_rejected(db: Session) -> None:
    add_user(db, email="mia@example.com")
    with pytest.raises(IntegrityError, match="uq_users_email"):
        add_user(db, email="mia@example.com")


def test_usernames_are_unique_regardless_of_case(db: Session) -> None:
    add_user(db, username="Mia")
    with pytest.raises(IntegrityError, match="uq_users_username_lower"):
        add_user(db, username="mia")


@pytest.mark.parametrize("username", ["ab", "a" * 31])
def test_username_length_is_checked(db: Session, username: str) -> None:
    with pytest.raises(IntegrityError, match="ck_users_username_length"):
        add_user(db, username=username)


def test_unknown_role_is_rejected(db: Session) -> None:
    with pytest.raises(IntegrityError, match="ck_users_role"):
        add_user(db, role="teacher")


def test_unknown_practice_language_is_rejected(db: Session) -> None:
    with pytest.raises(IntegrityError, match="ck_users_preferred_language"):
        add_user(db, preferred_language="en")


def test_current_class_must_exist(db: Session) -> None:
    with pytest.raises(IntegrityError, match="fk_users_current_class_id_classes"):
        add_user(db, current_class_id=uuid4())


def test_unknown_class_language_is_rejected(db: Session) -> None:
    with pytest.raises(IntegrityError, match="ck_classes_target_language"):
        add_class(db, target_language="en")


def test_semester_must_be_a_term_code(db: Session) -> None:
    with pytest.raises(IntegrityError, match="ck_classes_semester_format"):
        add_class(db, semester="Fall 2026")


def test_a_class_has_at_most_one_owner_but_many_assistants(db: Session) -> None:
    school_class = add_class(db)
    add_staff(db, school_class, add_user(db, role=UserRole.STAFF), ClassStaffRole.OWNER)
    add_staff(db, school_class, add_user(db, role=UserRole.STAFF), ClassStaffRole.ASSISTANT)
    add_staff(db, school_class, add_user(db), ClassStaffRole.ASSISTANT)
    with pytest.raises(IntegrityError, match="uq_class_staff_one_owner"):
        add_staff(db, school_class, add_user(db, role=UserRole.STAFF), ClassStaffRole.OWNER)


def test_a_user_can_own_several_classes(db: Session) -> None:
    teacher = add_user(db, role=UserRole.STAFF)
    add_staff(db, add_class(db), teacher, ClassStaffRole.OWNER)
    add_staff(db, add_class(db, target_language=Language.FR), teacher, ClassStaffRole.OWNER)


def test_unknown_staff_role_is_rejected(db: Session) -> None:
    with pytest.raises(IntegrityError, match="ck_class_staff_role"):
        add_staff(db, add_class(db), add_user(db), "teacher")
