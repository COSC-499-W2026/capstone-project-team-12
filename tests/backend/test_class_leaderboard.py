"""Tests for A14 class leaderboard scoring (Issue #88)."""

from collections.abc import Callable
from dataclasses import FrozenInstanceError, asdict
from datetime import UTC, datetime
from uuid import UUID, uuid4

import pytest
from sqlalchemy.orm import Session

from app.engagement.leaderboard import LeaderboardEntry, get_class_leaderboard
from app.models.accounts import User
from app.models.engagement import Class, ClassEnrollment, PointsLedger
from app.models.enums import EnrollmentEndReason, Language


def add_points(
    db: Session,
    student: User,
    points: int,
    class_id: UUID | None,
    language: Language = Language.JA,
) -> None:
    """Create a unique points-ledger award."""
    db.add(
        PointsLedger(
            user_id=student.id,
            source_type="location",
            source_ref=str(uuid4()),
            class_id=class_id,
            language=language,
            points=points,
        )
    )
    db.flush()


def test_leaderboard_ranks_students_by_class_points(
    db: Session,
    make_user: Callable[..., User],
    make_class: Callable[..., Class],
    make_enrollment: Callable[..., ClassEnrollment],
) -> None:
    school_class = make_class(target_language=Language.JA)
    alice = make_user(username="Alice")
    bob = make_user(username="Bob")
    carol = make_user(username="Carol")

    for student in (alice, bob, carol):
        make_enrollment(student, school_class)

    add_points(db, alice, 100, school_class.id)
    add_points(db, alice, 200, school_class.id)
    add_points(db, bob, 500, school_class.id)
    add_points(db, carol, 150, school_class.id)

    entries = get_class_leaderboard(school_class.id, db, viewer_id=alice.id)

    assert [asdict(entry) for entry in entries] == [
        {"rank": 1, "username": "Bob", "total_points": 500, "is_me": False},
        {"rank": 2, "username": "Alice", "total_points": 300, "is_me": True},
        {"rank": 3, "username": "Carol", "total_points": 150, "is_me": False},
    ]


def test_practice_points_are_excluded(
    db: Session,
    make_user: Callable[..., User],
    make_class: Callable[..., Class],
    make_enrollment: Callable[..., ClassEnrollment],
) -> None:
    school_class = make_class(target_language=Language.JA)
    student = make_user()
    make_enrollment(student, school_class)

    add_points(db, student, 100, school_class.id)
    add_points(db, student, 900, None)

    entries = get_class_leaderboard(school_class.id, db)

    assert len(entries) == 1
    assert entries[0].total_points == 100


def test_other_class_points_are_excluded(
    db: Session,
    make_user: Callable[..., User],
    make_class: Callable[..., Class],
    make_enrollment: Callable[..., ClassEnrollment],
) -> None:
    first_class = make_class(target_language=Language.JA)
    second_class = make_class(target_language=Language.FR)
    student = make_user()
    make_enrollment(student, first_class)

    add_points(db, student, 100, first_class.id, Language.JA)
    add_points(db, student, 500, second_class.id, Language.FR)

    entries = get_class_leaderboard(first_class.id, db)

    assert len(entries) == 1
    assert entries[0].total_points == 100


def test_zero_point_students_are_included(
    db: Session,
    make_user: Callable[..., User],
    make_class: Callable[..., Class],
    make_enrollment: Callable[..., ClassEnrollment],
) -> None:
    school_class = make_class()
    student = make_user()
    make_enrollment(student, school_class)

    entries = get_class_leaderboard(school_class.id, db)

    assert len(entries) == 1
    assert entries[0].username == student.username
    assert entries[0].rank == 1
    assert entries[0].total_points == 0
    assert entries[0].is_me is False


def test_tied_students_share_rank(
    db: Session,
    make_user: Callable[..., User],
    make_class: Callable[..., Class],
    make_enrollment: Callable[..., ClassEnrollment],
) -> None:
    school_class = make_class(target_language=Language.JA)
    alice = make_user(username="Alice")
    bob = make_user(username="Bob")
    carol = make_user(username="Carol")

    for student in (alice, bob, carol):
        make_enrollment(student, school_class)

    add_points(db, alice, 200, school_class.id)
    add_points(db, bob, 200, school_class.id)
    add_points(db, carol, 100, school_class.id)

    entries = get_class_leaderboard(school_class.id, db)

    assert [(entry.username, entry.rank) for entry in entries] == [
        ("Alice", 1),
        ("Bob", 1),
        ("Carol", 3),
    ]


def test_inactive_enrollments_are_excluded(
    db: Session,
    make_user: Callable[..., User],
    make_class: Callable[..., Class],
    make_enrollment: Callable[..., ClassEnrollment],
) -> None:
    school_class = make_class(target_language=Language.JA)
    active_student = make_user()
    former_student = make_user()

    make_enrollment(active_student, school_class)
    enrollment = make_enrollment(former_student, school_class)
    db.refresh(enrollment)
    enrollment.is_active = False
    enrollment.end_reason = EnrollmentEndReason.LEFT
    enrollment.left_at = max(datetime.now(UTC), enrollment.joined_at)
    db.flush()

    add_points(db, active_student, 100, school_class.id)
    add_points(db, former_student, 500, school_class.id)

    entries = get_class_leaderboard(school_class.id, db)

    assert len(entries) == 1
    assert entries[0].username == active_student.username


def test_unenrolled_students_are_excluded(
    db: Session,
    make_user: Callable[..., User],
    make_class: Callable[..., Class],
    make_enrollment: Callable[..., ClassEnrollment],
) -> None:
    school_class = make_class(target_language=Language.JA)
    enrolled = make_user()
    outsider = make_user()
    make_enrollment(enrolled, school_class)

    add_points(db, enrolled, 100, school_class.id)
    add_points(db, outsider, 500, school_class.id)

    entries = get_class_leaderboard(school_class.id, db)

    assert len(entries) == 1
    assert entries[0].username == enrolled.username


def test_empty_class_has_empty_leaderboard(
    db: Session,
    make_class: Callable[..., Class],
) -> None:
    school_class = make_class()

    assert get_class_leaderboard(school_class.id, db) == []


def test_leaderboard_entry_is_immutable() -> None:
    entry = LeaderboardEntry(1, "Alice", 100, False)

    with pytest.raises(FrozenInstanceError):
        entry.total_points = 200  # type: ignore[misc]
