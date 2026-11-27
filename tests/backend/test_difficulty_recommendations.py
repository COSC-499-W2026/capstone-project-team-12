"""A16 teacher difficulty hints: minimum sample, thresholds and isolation."""

from collections.abc import Callable
from datetime import UTC, datetime

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import event
from sqlalchemy.orm import Session

from app.engagement.interface import get_class_student_analytics
from app.models.enums import Difficulty, LocationStatus, SessionState, UserRole


def _add_results(
    make_game_session, make_session_location, student, school_class, difficulty, successes, total
):
    """Create finished classroom locations, five per closed game session."""
    for offset in range(0, total, 5):
        game = make_game_session(
            student=student,
            class_id=school_class.id,
            difficulty=difficulty,
            state=SessionState.COMPLETED,
            completed_at=datetime.now(UTC),
        )
        for position in range(1, min(5, total - offset) + 1):
            is_success = offset + position <= successes
            fields = {
                "status": LocationStatus.COMPLETED if is_success else LocationStatus.FAILED,
            }
            if is_success:
                fields.update(points_awarded=100, completed_at=datetime.now(UTC))
            make_session_location(game, position, **fields)


@pytest.mark.parametrize(
    ("difficulty", "completed", "finished", "expected"),
    [
        (Difficulty.ADVANCING, 0, 4, None),  # Not enough finished results
        (Difficulty.ADVANCING, 1, 5, "suggest_easier"),  # 20% < 40%
        (Difficulty.ADVANCING, 2, 5, None),  # Exactly 40%: no hint
        (Difficulty.ADVANCING, 4, 5, None),  # 80%: no hint
        (Difficulty.ADVANCING, 17, 20, None),  # Exactly 85%: no hint
        (Difficulty.BASIC, 0, 5, None),
        (Difficulty.CHALLENGING, 5, 5, None),
        (Difficulty.ADVANCING, 5, 5, "suggest_harder"),  # 100% > 85%
    ],
)
def test_hint_thresholds(
    db: Session,
    make_user: Callable,
    make_class: Callable,
    make_enrollment: Callable,
    make_game_session: Callable,
    make_session_location: Callable,
    difficulty: Difficulty,
    completed: int,
    finished: int,
    expected: str | None,
) -> None:
    student = make_user()
    school_class = make_class()
    make_enrollment(student, school_class)
    _add_results(
        make_game_session,
        make_session_location,
        student,
        school_class,
        difficulty,
        completed,
        finished,
    )

    hints = get_class_student_analytics(school_class.id, db)[0].difficulty_hints
    assert [hint.difficulty for hint in hints] == list(Difficulty)
    hint = hints[difficulty.value - 1]
    assert hint.finished == finished
    assert hint.completed == completed
    assert hint.failed == finished - completed
    assert hint.suggestion == expected
    assert hint.success_rate == round(completed / finished * 100, 2)
    assert all(other.suggestion is None for other in hints if other.difficulty != difficulty)


def test_hints_are_independent_per_difficulty(
    db: Session,
    make_user: Callable,
    make_class: Callable,
    make_enrollment: Callable,
    make_game_session: Callable,
    make_session_location: Callable,
) -> None:
    student = make_user()
    school_class = make_class()
    make_enrollment(student, school_class)
    _add_results(
        make_game_session, make_session_location, student, school_class, Difficulty.BASIC, 5, 5
    )
    _add_results(
        make_game_session, make_session_location, student, school_class, Difficulty.ADVANCING, 1, 5
    )
    hints = get_class_student_analytics(school_class.id, db)[0].difficulty_hints
    assert [hint.suggestion for hint in hints] == ["suggest_harder", "suggest_easier", None]
    assert hints[2].success_rate is None
    assert hints[2].finished == 0


def test_practice_and_other_class_results_excluded(
    db: Session,
    make_user: Callable,
    make_class: Callable,
    make_enrollment: Callable,
    make_game_session: Callable,
    make_session_location: Callable,
) -> None:
    student = make_user()
    school_class = make_class()
    other_class = make_class()
    make_enrollment(student, school_class)
    _add_results(
        make_game_session, make_session_location, student, other_class, Difficulty.BASIC, 5, 5
    )
    practice = make_game_session(
        student=student,
        difficulty=Difficulty.BASIC,
        state=SessionState.COMPLETED,
        completed_at=datetime.now(UTC),
    )
    for position in range(1, 6):
        make_session_location(
            practice,
            position,
            status=LocationStatus.COMPLETED,
            points_awarded=100,
            completed_at=datetime.now(UTC),
        )
    hints = get_class_student_analytics(school_class.id, db)[0].difficulty_hints
    assert all(hint.finished == 0 and hint.suggestion is None for hint in hints)


def test_student_endpoint_includes_difficulty_hints(
    client: TestClient,
    db: Session,
    auth_headers: Callable,
    make_user: Callable,
    make_class: Callable,
    make_class_staff: Callable,
    make_enrollment: Callable,
    make_game_session: Callable,
    make_session_location: Callable,
) -> None:
    teacher = make_user(role=UserRole.STAFF)
    student = make_user()
    school_class = make_class()
    make_class_staff(school_class, teacher)
    make_enrollment(student, school_class)
    _add_results(
        make_game_session, make_session_location, student, school_class, Difficulty.BASIC, 1, 5
    )
    for _ in range(20):
        make_enrollment(make_user(), school_class)

    statements: list[str] = []

    def count_statement(conn, cursor, statement, parameters, context, executemany):
        statements.append(statement)

    connection = db.connection()
    event.listen(connection, "before_cursor_execute", count_statement)
    try:
        response = client.get(
            f"/api/v1/teacher/classes/{school_class.id}/students", headers=auth_headers(teacher)
        )
    finally:
        event.remove(connection, "before_cursor_execute", count_statement)

    assert (
        sum("GROUP BY game_sessions.student_id, game_sessions.difficulty" in s for s in statements)
        == 1
    )
    assert len(statements) <= 10
    assert response.status_code == 200, response.text
    rows = response.json()
    assert len(rows) == 21
    student_row = next(row for row in rows if row["student_id"] == str(student.id))
    hints = student_row["difficulty_hints"]
    assert len(hints) == 3
    assert hints[0] == {
        "difficulty": 1,
        "finished": 5,
        "completed": 1,
        "failed": 4,
        "success_rate": 20.0,
        "suggestion": None,
    }
    assert hints[1]["suggestion"] is None
    assert hints[2]["suggestion"] is None
