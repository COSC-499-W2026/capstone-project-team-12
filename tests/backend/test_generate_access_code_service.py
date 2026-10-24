"""Tests for classroom access-code generation."""

from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.engagement.interface import generate_access_code
from app.errors import AppError, ErrorCode
from app.models.accounts import User
from app.models.engagement import (
    ACCESS_CODE_ALPHABET,
    ACCESS_CODE_LENGTH,
    AccessCode,
    Class,
)
from app.models.enums import ClassStaffRole, UserRole


@pytest.fixture
def expires_at() -> datetime:
    return datetime.now(UTC) + timedelta(days=30)


def test_owner_can_generate_code(
    db: Session,
    make_user: Callable[..., User],
    make_class: Callable[..., Class],
    make_class_staff: Callable[..., object],
    expires_at: datetime,
) -> None:
    owner = make_user(role=UserRole.STAFF)
    school_class = make_class()
    make_class_staff(school_class, owner)

    result = generate_access_code(school_class.id, owner, expires_at, db)

    assert len(result.code) == ACCESS_CODE_LENGTH
    assert set(result.code).issubset(set(ACCESS_CODE_ALPHABET))
    assert result.class_id == school_class.id
    assert result.created_by == owner.id
    assert result.is_active
    assert result.uses_count == 0
    assert result.max_uses is None
    assert db.get(AccessCode, result.id) is not None


def test_assistant_can_generate_code(
    db: Session,
    make_user: Callable[..., User],
    make_class: Callable[..., Class],
    make_class_staff: Callable[..., object],
    expires_at: datetime,
) -> None:
    assistant = make_user(role=UserRole.STUDENT)
    school_class = make_class()
    make_class_staff(school_class, assistant, role=ClassStaffRole.ASSISTANT)

    result = generate_access_code(school_class.id, assistant, expires_at, db)

    assert result.created_by == assistant.id


def test_student_cannot_generate_code(
    db: Session,
    make_user: Callable[..., User],
    make_class: Callable[..., Class],
    expires_at: datetime,
) -> None:
    student = make_user(role=UserRole.STUDENT)
    school_class = make_class()

    with pytest.raises(AppError) as exc:
        generate_access_code(school_class.id, student, expires_at, db)

    assert exc.value.code == ErrorCode.FORBIDDEN_ROLE


def test_unrelated_teacher_cannot_generate_code(
    db: Session,
    make_user: Callable[..., User],
    make_class: Callable[..., Class],
    expires_at: datetime,
) -> None:
    teacher = make_user(role=UserRole.STAFF)
    school_class = make_class()

    with pytest.raises(AppError) as exc:
        generate_access_code(school_class.id, teacher, expires_at, db)

    assert exc.value.code == ErrorCode.FORBIDDEN_ROLE


def test_archived_class_rejected(
    db: Session,
    make_user: Callable[..., User],
    make_class: Callable[..., Class],
    make_class_staff: Callable[..., object],
    expires_at: datetime,
) -> None:
    owner = make_user(role=UserRole.STAFF)
    school_class = make_class(archived_at=datetime.now(UTC))
    make_class_staff(school_class, owner)

    with pytest.raises(AppError) as exc:
        generate_access_code(school_class.id, owner, expires_at, db)

    assert exc.value.code == ErrorCode.CLASS_ARCHIVED


@pytest.mark.parametrize(
    "offset",
    [timedelta(days=-1), timedelta(seconds=-1)],
)
def test_past_expiration_rejected(
    offset: timedelta,
    db: Session,
    make_user: Callable[..., User],
    make_class: Callable[..., Class],
    make_class_staff: Callable[..., object],
) -> None:
    owner = make_user(role=UserRole.STAFF)
    school_class = make_class()
    make_class_staff(school_class, owner)

    with pytest.raises(AppError) as exc:
        generate_access_code(
            school_class.id,
            owner,
            datetime.now(UTC) + offset,
            db,
        )

    assert exc.value.code == ErrorCode.VALIDATION_ERROR


def test_naive_expiration_rejected(
    db: Session,
    make_user: Callable[..., User],
    make_class: Callable[..., Class],
    make_class_staff: Callable[..., object],
) -> None:
    owner = make_user(role=UserRole.STAFF)
    school_class = make_class()
    make_class_staff(school_class, owner)

    naive_date = datetime.now(UTC).replace(tzinfo=None) + timedelta(days=1)

    with pytest.raises(AppError) as exc:
        generate_access_code(school_class.id, owner, naive_date, db)

    assert exc.value.code == ErrorCode.VALIDATION_ERROR


@pytest.mark.parametrize("max_uses", [0, -1])
def test_invalid_usage_limit_rejected(
    max_uses: int,
    db: Session,
    make_user: Callable[..., User],
    make_class: Callable[..., Class],
    make_class_staff: Callable[..., object],
    expires_at: datetime,
) -> None:
    owner = make_user(role=UserRole.STAFF)
    school_class = make_class()
    make_class_staff(school_class, owner)

    with pytest.raises(AppError) as exc:
        generate_access_code(
            school_class.id,
            owner,
            expires_at,
            db,
            max_uses=max_uses,
        )

    assert exc.value.code == ErrorCode.VALIDATION_ERROR


def test_usage_limit_saved(
    db: Session,
    make_user: Callable[..., User],
    make_class: Callable[..., Class],
    make_class_staff: Callable[..., object],
    expires_at: datetime,
) -> None:
    owner = make_user(role=UserRole.STAFF)
    school_class = make_class()
    make_class_staff(school_class, owner)

    result = generate_access_code(
        school_class.id,
        owner,
        expires_at,
        db,
        max_uses=25,
    )

    assert result.max_uses == 25


def test_missing_class_rejected(
    db: Session,
    make_user: Callable[..., User],
    expires_at: datetime,
) -> None:
    owner = make_user(role=UserRole.STAFF)

    with pytest.raises(AppError) as exc:
        generate_access_code(uuid4(), owner, expires_at, db)

    assert exc.value.code == ErrorCode.NOT_FOUND


def test_inactive_owner_rejected(
    db: Session,
    make_user: Callable[..., User],
    make_class: Callable[..., Class],
    make_class_staff: Callable[..., object],
    expires_at: datetime,
) -> None:
    owner = make_user(role=UserRole.STAFF, is_active=False)
    school_class = make_class()
    make_class_staff(school_class, owner)

    with pytest.raises(AppError) as exc:
        generate_access_code(school_class.id, owner, expires_at, db)

    assert exc.value.code == ErrorCode.ACCOUNT_DISABLED


def test_multiple_codes_are_unique(
    db: Session,
    make_user: Callable[..., User],
    make_class: Callable[..., Class],
    make_class_staff: Callable[..., object],
    expires_at: datetime,
) -> None:
    owner = make_user(role=UserRole.STAFF)
    school_class = make_class()
    make_class_staff(school_class, owner)

    codes = [generate_access_code(school_class.id, owner, expires_at, db).code for _ in range(20)]

    assert len(codes) == len(set(codes))


def test_generation_does_not_commit(
    db: Session,
    make_user: Callable[..., User],
    make_class: Callable[..., Class],
    make_class_staff: Callable[..., object],
    expires_at: datetime,
    transaction_calls: list[str],
) -> None:
    owner = make_user(role=UserRole.STAFF)
    school_class = make_class()
    make_class_staff(school_class, owner)

    transaction_calls.clear()

    result = generate_access_code(school_class.id, owner, expires_at, db)

    assert result.id is not None
    assert db.execute(select(AccessCode).where(AccessCode.id == result.id)).scalar_one() is result
    assert "commit" not in transaction_calls
    assert "rollback" not in transaction_calls


def test_existing_code_collision_is_retried(
    db: Session,
    make_user: Callable[..., User],
    make_class: Callable[..., Class],
    make_class_staff: Callable[..., object],
    make_access_code: Callable[..., AccessCode],
    expires_at: datetime,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    owner = make_user(role=UserRole.STAFF)
    school_class = make_class()
    make_class_staff(school_class, owner)

    existing = make_access_code(
        school_class,
        code="AAAAAAAA",
    )

    generated_characters = iter("AAAAAAAA" + "BBBBBBBB")

    monkeypatch.setattr(
        "app.engagement.classes.secrets.choice",
        lambda _alphabet: next(generated_characters),
    )

    result = generate_access_code(
        school_class.id,
        owner,
        expires_at,
        db,
    )

    assert result.code == "BBBBBBBB"
    assert result.code != existing.code


@pytest.mark.parametrize(
    ("term", "end_month"),
    [
        ("W1", 1),
        ("W2", 5),
        ("S1", 7),
        ("S2", 9),
    ],
)
def test_default_expiration_is_semester_end(
    term: str,
    end_month: int,
    db: Session,
    make_user: Callable[..., User],
    make_class: Callable[..., Class],
    make_class_staff: Callable[..., object],
) -> None:
    year = datetime.now(UTC).year + 1
    semester = f"{year}{term}"
    expected_year = year + 1 if term in ("W1", "W2") else year
    expected = datetime(expected_year, end_month, 1, 7, tzinfo=UTC)

    owner = make_user(role=UserRole.STAFF)
    school_class = make_class(semester=semester)
    make_class_staff(school_class, owner)

    result = generate_access_code(school_class.id, owner, None, db)

    assert result.expires_at == expected
