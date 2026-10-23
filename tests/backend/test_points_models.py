"""Database tests for the points ledger and language statistics (Issue #86)."""

from collections.abc import Callable
from uuid import uuid4

import pytest
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.models.accounts import User
from app.models.engagement import Class, PointsLedger, UserLanguageStats
from app.models.enums import Language, PointsSourceType


def test_points_ledger_records_location_award(db: Session, make_user: Callable[..., User]) -> None:
    student = make_user()
    award = PointsLedger(
        user_id=student.id,
        source_type=PointsSourceType.LOCATION,
        source_ref="location-1",
        class_id=None,
        language=Language.JA,
        points=100,
    )
    db.add(award)
    db.flush()

    assert award.id is not None
    assert award.points == 100
    assert award.language == Language.JA


def test_duplicate_location_award_is_rejected(db: Session, make_user: Callable[..., User]) -> None:
    student = make_user()
    for _ in range(2):
        db.add(
            PointsLedger(
                user_id=student.id,
                source_type=PointsSourceType.LOCATION,
                source_ref="same-location",
                language=Language.JA,
                points=100,
            )
        )

    with pytest.raises(IntegrityError):
        db.flush()


def test_points_are_tracked_separately_by_language(
    db: Session, make_user: Callable[..., User]
) -> None:
    student = make_user()
    db.add_all(
        [
            UserLanguageStats(user_id=student.id, language=Language.JA, total_points=200),
            UserLanguageStats(user_id=student.id, language=Language.FR, total_points=100),
        ]
    )
    db.flush()

    japanese = db.get(UserLanguageStats, (student.id, Language.JA))
    french = db.get(UserLanguageStats, (student.id, Language.FR))

    assert japanese is not None
    assert french is not None
    assert japanese.total_points == 200
    assert french.total_points == 100


def test_negative_points_are_rejected(db: Session, make_user: Callable[..., User]) -> None:
    student = make_user()
    db.add(
        PointsLedger(
            user_id=student.id,
            source_type=PointsSourceType.LOCATION,
            source_ref="invalid-award",
            language=Language.JA,
            points=-100,
        )
    )

    with pytest.raises(IntegrityError):
        db.flush()


def test_negative_language_total_is_rejected(db: Session, make_user: Callable[..., User]) -> None:
    student = make_user()
    db.add(
        UserLanguageStats(
            user_id=student.id,
            language=Language.JA,
            total_points=-1,
        )
    )

    with pytest.raises(IntegrityError):
        db.flush()


@pytest.mark.parametrize(
    ("source_type", "language"),
    [
        ("unknown_source", Language.JA),
        (PointsSourceType.LOCATION, "invalid_language"),
    ],
)
def test_invalid_ledger_values_rejected(
    db: Session,
    make_user: Callable[..., User],
    source_type: str,
    language: str,
) -> None:
    student = make_user()

    db.add(
        PointsLedger(
            user_id=student.id,
            source_type=source_type,
            source_ref="invalid-value",
            language=language,
            points=100,
        )
    )

    with pytest.raises(IntegrityError):
        db.flush()


def test_nonexistent_class_rejected(
    db: Session,
    make_user: Callable[..., User],
) -> None:
    student = make_user()

    db.add(
        PointsLedger(
            user_id=student.id,
            source_type=PointsSourceType.LOCATION,
            source_ref="missing-class",
            class_id=uuid4(),
            language=Language.JA,
            points=100,
        )
    )

    with pytest.raises(IntegrityError):
        db.flush()


def test_class_language_mismatch_rejected(
    db: Session,
    make_user: Callable[..., User],
) -> None:
    student = make_user()
    school_class = Class(
        name="Japanese 101",
        semester="2026W1",
        target_language=Language.JA,
    )
    db.add(school_class)
    db.flush()

    db.add(
        PointsLedger(
            user_id=student.id,
            source_type=PointsSourceType.LOCATION,
            source_ref="wrong-class-language",
            class_id=school_class.id,
            language=Language.FR,
            points=100,
        )
    )

    with pytest.raises(IntegrityError):
        db.flush()
