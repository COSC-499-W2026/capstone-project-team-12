"""Unit-level database tests for classroom settings updates. The route checks who may update."""

from collections.abc import Callable
from datetime import UTC, datetime
from uuid import uuid4

import pytest
from sqlalchemy.orm import Session

from app.engagement.classes import update_class_settings
from app.errors import AppError, ErrorCode
from app.models.engagement import Class


def test_name_and_leaderboard_are_updated(db: Session, make_class: Callable[..., Class]) -> None:
    school_class = make_class()
    original_language = school_class.target_language
    original_semester = school_class.semester

    result = update_class_settings(
        school_class.id, db, name="  Updated Classroom  ", leaderboard_enabled=False
    )

    assert result.id == school_class.id
    assert result.name == "Updated Classroom"
    assert result.leaderboard_enabled is False
    assert result.target_language == original_language
    assert result.semester == original_semester

    db.refresh(school_class)
    assert school_class.name == "Updated Classroom"


def test_archived_class_cannot_be_updated(db: Session, make_class: Callable[..., Class]) -> None:
    school_class = make_class(archived_at=datetime.now(UTC))

    with pytest.raises(AppError) as exc:
        update_class_settings(school_class.id, db, name="Changed")

    assert exc.value.code == ErrorCode.CLASS_ARCHIVED


@pytest.mark.parametrize(
    "name, message",
    [
        ("   ", "Class name cannot be empty"),
        ("Bad\x00Name", "Class name cannot contain NUL characters"),
    ],
)
def test_a_bad_name_is_rejected(
    db: Session, make_class: Callable[..., Class], name: str, message: str
) -> None:
    with pytest.raises(AppError) as exc:
        update_class_settings(make_class().id, db, name=name)

    assert exc.value.code == ErrorCode.VALIDATION_ERROR
    assert exc.value.details == [{"in": "body", "field": "name", "message": message}]


def test_update_class_settings_does_not_manage_transaction(
    db: Session, make_class: Callable[..., Class], transaction_calls: list[str]
) -> None:
    school_class = make_class()
    transaction_calls.clear()

    update_class_settings(school_class.id, db, name="Updated")

    assert "commit" not in transaction_calls
    assert "rollback" not in transaction_calls


def test_missing_class_returns_not_found(db: Session) -> None:
    with pytest.raises(AppError) as exc:
        update_class_settings(uuid4(), db, name="Updated")

    assert exc.value.code == ErrorCode.NOT_FOUND
