"""A16 participation grading: distinct completions, dates, and class isolation."""

from datetime import UTC, date, datetime

import pytest

from app.config import get_settings
from app.engagement import class_statistics
from app.engagement.class_statistics import get_class_participation
from app.errors import AppError, ErrorCode
from app.models.enums import Difficulty, LocationStatus, SessionState, UserRole


def _setup(make_user, make_class, make_class_staff, make_enrollment):
    teacher = make_user(role=UserRole.STAFF)
    student = make_user()
    classroom = make_class()
    make_class_staff(classroom, teacher)
    make_enrollment(student, classroom)
    return teacher, student, classroom


def _location(
    make_game_session,
    make_session_location,
    student,
    classroom,
    *,
    difficulty=Difficulty.BASIC,
    location_id=None,
    completed_at=None,
    status=LocationStatus.COMPLETED,
):
    game = make_game_session(
        student=student,
        class_id=classroom.id if classroom is not None else None,
        difficulty=difficulty,
        state=SessionState.COMPLETED,
        completed_at=datetime.now(UTC),
    )
    fields = {"status": status}
    if status == LocationStatus.COMPLETED:
        fields.update(points_awarded=100, completed_at=completed_at or datetime.now(UTC))
    location = make_session_location(game, 1, **fields)
    if location_id is not None:
        location.location_id = location_id
    return location


def test_unique_pairs_and_difficulty_count_separately(
    db,
    make_user,
    make_class,
    make_class_staff,
    make_enrollment,
    make_game_session,
    make_session_location,
    make_location,
):
    _, student, classroom = _setup(make_user, make_class, make_class_staff, make_enrollment)
    shared_id = make_location().id
    for difficulty in (Difficulty.BASIC, Difficulty.BASIC, Difficulty.ADVANCING):
        _location(
            make_game_session,
            make_session_location,
            student,
            classroom,
            difficulty=difficulty,
            location_id=shared_id,
        )
    rows = get_class_participation(classroom.id, db, date(2020, 1, 1), date(2030, 1, 1))
    assert len(rows) == 1
    assert rows[0].distinct_completed_locations == 2
    assert rows[0].target_locations == 10
    assert rows[0].participation_percent == 20


def test_date_boundaries_use_configured_timezone(
    db,
    monkeypatch,
    make_user,
    make_class,
    make_class_staff,
    make_enrollment,
    make_game_session,
    make_session_location,
):
    _, student, classroom = _setup(make_user, make_class, make_class_staff, make_enrollment)
    settings = get_settings().model_copy(update={"app_timezone": "UTC"})
    monkeypatch.setattr(class_statistics, "get_settings", lambda: settings)
    for timestamp in (
        datetime(2026, 11, 13, 23, 59, tzinfo=UTC),
        datetime(2026, 11, 14, 0, 0, tzinfo=UTC),
        datetime(2026, 11, 14, 23, 59, tzinfo=UTC),
        datetime(2026, 11, 15, 0, 0, tzinfo=UTC),
    ):
        _location(
            make_game_session, make_session_location, student, classroom, completed_at=timestamp
        )
    rows = get_class_participation(classroom.id, db, date(2026, 11, 14), date(2026, 11, 14))
    assert rows[0].distinct_completed_locations == 2
    assert rows[0].participation_percent == 20


def test_excludes_failed_practice_and_other_classes(
    db,
    make_user,
    make_class,
    make_class_staff,
    make_enrollment,
    make_game_session,
    make_session_location,
):
    _, student, classroom = _setup(make_user, make_class, make_class_staff, make_enrollment)
    _location(
        make_game_session, make_session_location, student, classroom, status=LocationStatus.FAILED
    )
    _location(make_game_session, make_session_location, student, None)
    _location(make_game_session, make_session_location, student, make_class())
    rows = get_class_participation(classroom.id, db, date(2020, 1, 1), date(2030, 1, 1))
    assert rows[0].distinct_completed_locations == 0
    assert rows[0].participation_percent == 0


@pytest.mark.parametrize(("completed", "target", "percent"), [(5, 10, 50), (1, 3, 33), (3, 2, 100)])
def test_former_student_participation_percentage(
    db,
    make_user,
    make_class,
    make_enrollment,
    end_enrolment,
    make_game_session,
    make_session_location,
    completed,
    target,
    percent,
):
    student = make_user()
    classroom = make_class()
    enrollment = make_enrollment(student, classroom)
    for _ in range(completed):
        _location(make_game_session, make_session_location, student, classroom)
    end_enrolment(enrollment)
    rows = get_class_participation(
        classroom.id,
        db,
        date(2020, 1, 1),
        date(2030, 1, 1),
        target_locations=target,
    )
    assert len(rows) == 1
    assert rows[0].student_id == student.id
    assert rows[0].distinct_completed_locations == completed
    assert rows[0].participation_percent == percent
    assert rows[0].is_active_enrollment is False
    assert rows[0].enrollment_status == "left"


def test_zero_activity_student_is_included(
    db,
    make_user,
    make_class,
    make_class_staff,
    make_enrollment,
):
    _, student, classroom = _setup(make_user, make_class, make_class_staff, make_enrollment)
    rows = get_class_participation(classroom.id, db, date(2026, 11, 14), date(2026, 11, 14))
    assert len(rows) == 1
    assert rows[0].student_id == student.id
    assert rows[0].distinct_completed_locations == 0
    assert rows[0].participation_percent == 0


@pytest.mark.parametrize(
    "start,end,target",
    [
        (date(2026, 11, 15), date(2026, 11, 14), 10),
        (date(2026, 11, 14), date(2026, 11, 14), 0),
    ],
)
def test_rejects_invalid_parameters(
    db,
    make_user,
    make_class,
    make_class_staff,
    make_enrollment,
    start,
    end,
    target,
):
    _, _, classroom = _setup(make_user, make_class, make_class_staff, make_enrollment)
    with pytest.raises(AppError) as exc:
        get_class_participation(classroom.id, db, start, end, target)
    assert exc.value.code == ErrorCode.VALIDATION_ERROR
