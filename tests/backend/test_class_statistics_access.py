"""Authorization tests for A16 classroom statistics."""

from collections.abc import Callable
from datetime import UTC, datetime
from uuid import uuid4

import pytest
from sqlalchemy.orm import Session

from app.engagement.class_statistics import require_analytics_access
from app.errors import AppError, ErrorCode
from app.models.accounts import User
from app.models.engagement import Class
from app.models.enums import ClassStaffRole, UserRole


def test_owner_can_access_class(
    db: Session,
    make_user: Callable[..., User],
    make_class: Callable[..., Class],
    make_class_staff: Callable[..., object],
) -> None:
    teacher = make_user(role=UserRole.STAFF)
    school_class = make_class()
    make_class_staff(school_class, teacher)

    result = require_analytics_access(school_class.id, teacher, db)

    assert result.id == school_class.id


def test_student_account_ta_can_access_class(
    db: Session,
    make_user: Callable[..., User],
    make_class: Callable[..., Class],
    make_class_staff: Callable[..., object],
) -> None:
    ta = make_user(role=UserRole.STUDENT)
    school_class = make_class()
    make_class_staff(school_class, ta, role=ClassStaffRole.ASSISTANT)

    result = require_analytics_access(school_class.id, ta, db)

    assert result.id == school_class.id


def test_unassigned_user_is_forbidden(
    db: Session,
    make_user: Callable[..., User],
    make_class: Callable[..., Class],
) -> None:
    user = make_user(role=UserRole.STAFF)
    school_class = make_class()

    with pytest.raises(AppError) as exc:
        require_analytics_access(school_class.id, user, db)

    assert exc.value.code == ErrorCode.FORBIDDEN_ROLE


def test_nonexistent_class_is_not_found(
    db: Session,
    make_user: Callable[..., User],
) -> None:
    teacher = make_user(role=UserRole.STAFF)

    with pytest.raises(AppError) as exc:
        require_analytics_access(uuid4(), teacher, db)

    assert exc.value.code == ErrorCode.NOT_FOUND


def test_archived_class_remains_accessible(
    db: Session,
    make_user: Callable[..., User],
    make_class: Callable[..., Class],
    make_class_staff: Callable[..., object],
) -> None:
    teacher = make_user(role=UserRole.STAFF)
    school_class = make_class(archived_at=datetime(2026, 10, 1, tzinfo=UTC))
    make_class_staff(school_class, teacher)

    result = require_analytics_access(school_class.id, teacher, db)

    assert result.archived_at is not None


def test_inactive_account_is_rejected(
    db: Session,
    make_user: Callable[..., User],
    make_class: Callable[..., Class],
    make_class_staff: Callable[..., object],
) -> None:
    teacher = make_user(role=UserRole.STAFF)
    school_class = make_class()
    make_class_staff(school_class, teacher)
    teacher.is_active = False

    with pytest.raises(AppError) as exc:
        require_analytics_access(school_class.id, teacher, db)

    assert exc.value.code == ErrorCode.ACCOUNT_DISABLED


def test_authorization_is_read_only(
    db: Session,
    transaction_calls: list[str],
    make_user: Callable[..., User],
    make_class: Callable[..., Class],
    make_class_staff: Callable[..., object],
) -> None:
    teacher = make_user(role=UserRole.STAFF)
    school_class = make_class()
    make_class_staff(school_class, teacher)

    transaction_calls.clear()
    require_analytics_access(school_class.id, teacher, db)

    assert transaction_calls == []
