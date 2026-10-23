"""Tests for classroom creation service (Issue #84)."""

from collections.abc import Callable

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.engagement.classes import create_class
from app.errors import AppError, ErrorCode
from app.models.accounts import ClassStaff, User
from app.models.engagement import Class
from app.models.enums import ClassStaffRole, Language, UserRole


def test_teacher_creates_class(
    db: Session,
    make_user: Callable[..., User],
) -> None:
    teacher = make_user(role=UserRole.STAFF)

    school_class = create_class(
        teacher,
        "JAPN 101 - Fall 2026",
        "2026W1",
        Language.JA,
        db,
    )

    assert school_class.id is not None
    assert school_class.target_language == Language.JA
    assert school_class.semester == "2026W1"

    owner = db.scalar(
        select(ClassStaff).where(
            ClassStaff.class_id == school_class.id,
            ClassStaff.user_id == teacher.id,
        )
    )
    assert owner is not None
    assert owner.role == ClassStaffRole.OWNER


def test_student_cannot_create_class(
    db: Session,
    make_user: Callable[..., User],
) -> None:
    student = make_user()

    with pytest.raises(AppError) as exc:
        create_class(student, "Test", "2026W1", Language.JA, db)

    assert exc.value.code == ErrorCode.FORBIDDEN_ROLE
    assert db.scalars(select(Class)).all() == []


@pytest.mark.parametrize("semester", ["2026F", "2026W3", "invalid", ""])
def test_invalid_semester(
    db: Session,
    make_user: Callable[..., User],
    semester: str,
) -> None:
    teacher = make_user(role=UserRole.STAFF)

    with pytest.raises(AppError) as exc:
        create_class(teacher, "Test", semester, Language.JA, db)

    assert exc.value.code == ErrorCode.VALIDATION_ERROR


def test_empty_class_name(
    db: Session,
    make_user: Callable[..., User],
) -> None:
    teacher = make_user(role=UserRole.STAFF)

    with pytest.raises(AppError) as exc:
        create_class(teacher, "   ", "2026W1", Language.JA, db)

    assert exc.value.code == ErrorCode.VALIDATION_ERROR


def test_disabled_teacher_cannot_create_class(
    db: Session,
    make_user: Callable[..., User],
) -> None:
    teacher = make_user(role=UserRole.STAFF, is_active=False)

    with pytest.raises(AppError) as exc:
        create_class(teacher, "Test", "2026W1", Language.JA, db)

    assert exc.value.code == ErrorCode.ACCOUNT_DISABLED


def test_creation_does_not_commit(
    db: Session,
    make_user: Callable[..., User],
    transaction_calls: list[str],
) -> None:
    teacher = make_user(role=UserRole.STAFF)
    transaction_calls.clear()

    create_class(teacher, "Test Class", "2026W1", Language.JA, db)

    assert "commit" not in transaction_calls
    assert "rollback" not in transaction_calls


def test_creation_assigns_exactly_one_owner(
    db: Session,
    make_user: Callable[..., User],
) -> None:
    teacher = make_user(role=UserRole.STAFF)

    school_class = create_class(teacher, "Test Class", "2026W1", Language.JA, db)

    owners = db.scalars(
        select(ClassStaff).where(
            ClassStaff.class_id == school_class.id,
            ClassStaff.role == ClassStaffRole.OWNER,
        )
    ).all()

    assert len(owners) == 1
    assert owners[0].user_id == teacher.id


def test_class_name_is_trimmed(
    db: Session,
    make_user: Callable[..., User],
) -> None:
    teacher = make_user(role=UserRole.STAFF)

    school_class = create_class(teacher, "  Japanese 101  ", "2026W1", Language.JA, db)

    db.refresh(school_class)
    assert school_class.name == "Japanese 101"


def test_class_name_rejects_nul_character(
    db: Session,
    make_user: Callable[..., User],
) -> None:
    teacher = make_user(role=UserRole.STAFF)

    with pytest.raises(AppError) as exc:
        create_class(teacher, "Japanese\x00 101", "2026W1", Language.JA, db)

    assert exc.value.code == ErrorCode.VALIDATION_ERROR
    assert exc.value.details[0]["field"] == "name"
    assert db.scalars(select(Class)).all() == []


@pytest.mark.parametrize(
    ("name", "semester", "expected_field"),
    [
        ("   ", "2026W1", "name"),
        ("Test", "invalid", "semester"),
    ],
)
def test_validation_error_identifies_field(
    db: Session,
    make_user: Callable[..., User],
    name: str,
    semester: str,
    expected_field: str,
) -> None:
    teacher = make_user(role=UserRole.STAFF)

    with pytest.raises(AppError) as exc:
        create_class(teacher, name, semester, Language.JA, db)

    assert exc.value.code == ErrorCode.VALIDATION_ERROR
    assert exc.value.details
    assert exc.value.details[0]["in"] == "body"
    assert exc.value.details[0]["field"] == expected_field
