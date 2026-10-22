"""Unit-level database tests for classroom settings updates."""

from collections.abc import Callable
from datetime import UTC, datetime

import pytest
from sqlalchemy.orm import Session

from app.engagement.classes import update_class_settings
from app.errors import AppError, ErrorCode
from app.models.accounts import ClassStaff, User
from app.models.engagement import Class
from app.models.enums import ClassStaffRole, UserRole


def test_owner_updates_name_and_leaderboard(
    db: Session,
    make_user: Callable[..., User],
    make_class: Callable[..., Class],
    make_class_staff: Callable[..., ClassStaff],
) -> None:
    owner = make_user(role=UserRole.STAFF)
    school_class = make_class()
    make_class_staff(school_class, owner)

    original_language = school_class.target_language
    original_semester = school_class.semester

    result = update_class_settings(
        school_class.id,
        owner,
        db,
        name="  Updated Classroom  ",
        leaderboard_enabled=False,
    )

    assert result.id == school_class.id
    assert result.name == "Updated Classroom"
    assert result.leaderboard_enabled is False
    assert result.target_language == original_language
    assert result.semester == original_semester

    db.refresh(school_class)
    assert school_class.name == "Updated Classroom"


def test_assistant_cannot_update_settings(
    db: Session,
    make_user: Callable[..., User],
    make_class: Callable[..., Class],
    make_class_staff: Callable[..., ClassStaff],
) -> None:
    owner = make_user(role=UserRole.STAFF)
    assistant = make_user(role=UserRole.STAFF)
    school_class = make_class()
    make_class_staff(school_class, owner)
    make_class_staff(school_class, assistant, role=ClassStaffRole.ASSISTANT)

    with pytest.raises(AppError) as exc:
        update_class_settings(school_class.id, assistant, db, name="Changed")

    assert exc.value.code == ErrorCode.FORBIDDEN_ROLE
    assert school_class.name != "Changed"


def test_archived_class_cannot_be_updated(
    db: Session,
    make_user: Callable[..., User],
    make_class: Callable[..., Class],
    make_class_staff: Callable[..., ClassStaff],
) -> None:
    owner = make_user(role=UserRole.STAFF)
    school_class = make_class(archived_at=datetime.now(UTC))
    make_class_staff(school_class, owner)

    with pytest.raises(AppError) as exc:
        update_class_settings(school_class.id, owner, db, name="Changed")

    assert exc.value.code == ErrorCode.CLASS_ARCHIVED


def test_blank_name_is_rejected(
    db: Session,
    make_user: Callable[..., User],
    make_class: Callable[..., Class],
    make_class_staff: Callable[..., ClassStaff],
) -> None:
    owner = make_user(role=UserRole.STAFF)
    school_class = make_class()
    make_class_staff(school_class, owner)

    with pytest.raises(AppError) as exc:
        update_class_settings(school_class.id, owner, db, name="   ")

    assert exc.value.code == ErrorCode.VALIDATION_ERROR


def test_inactive_owner_cannot_update_settings(
    db: Session,
    make_user: Callable[..., User],
    make_class: Callable[..., Class],
    make_class_staff: Callable[..., ClassStaff],
) -> None:
    owner = make_user(role=UserRole.STAFF, is_active=False)
    school_class = make_class()
    make_class_staff(school_class, owner)

    with pytest.raises(AppError) as exc:
        update_class_settings(school_class.id, owner, db, name="Changed")

    assert exc.value.code == ErrorCode.ACCOUNT_DISABLED
