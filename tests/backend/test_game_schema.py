"""game_sessions rejects bad data at the database level."""

from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

import pytest
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.models.accounts import User
from app.models.engagement import Class
from app.models.enums import Difficulty, Language, SessionEndReason, SessionState, UserRole
from app.models.game import GameSession


def add_user(db: Session) -> User:
    user = User(email=f"{uuid4().hex[:12]}@example.com", username=f"u_{uuid4().hex[:8]}")
    user.role = UserRole.STUDENT
    db.add(user)
    db.flush()
    return user


def add_class(db: Session, language: str = Language.JA) -> Class:
    school_class = Class(name="JAPN 101", semester="2026W1", target_language=language)
    db.add(school_class)
    db.flush()
    return school_class


def add_session(db: Session, student: User | None = None, **fields: Any) -> GameSession:
    values: dict[str, Any] = {
        "student_id": (student or add_user(db)).id,
        "target_language": Language.JA,
        "difficulty": Difficulty.BASIC,
    }
    values.update(fields)
    game = GameSession(**values)
    db.add(game)
    db.flush()
    return game


def end(game: GameSession, reason: str = SessionEndReason.QUIT) -> None:
    game.state = SessionState.ABANDONED
    game.end_reason = reason


# game_sessions


def test_raw_sql_inserts_get_the_server_defaults(db: Session) -> None:
    student = add_user(db)
    game = db.execute(
        text(
            "INSERT INTO game_sessions (student_id, target_language, difficulty)"
            " VALUES (:student_id, 'ja', 1) RETURNING id, state, current_position,"
            " resume_count, shuffles_used, total_score, started_at, last_activity_at"
        ),
        {"student_id": student.id},
    ).one()
    assert game.id is not None
    assert game.state == SessionState.ACTIVE
    assert game.current_position == 1
    assert (game.resume_count, game.shuffles_used, game.total_score) == (0, 0, 0)
    assert game.started_at is not None and game.last_activity_at is not None


def test_a_student_has_only_one_open_game(db: Session) -> None:
    student = add_user(db)
    add_session(db, student)
    with pytest.raises(IntegrityError, match="uq_game_sessions_one_open_per_student"):
        add_session(db, student)


def test_a_paused_game_also_counts_as_open(db: Session) -> None:
    student = add_user(db)
    add_session(db, student, state=SessionState.PAUSED, paused_at=datetime.now(UTC))
    with pytest.raises(IntegrityError, match="uq_game_sessions_one_open_per_student"):
        add_session(db, student)


def test_a_new_game_can_start_after_the_old_one_ended(db: Session) -> None:
    student = add_user(db)
    end(add_session(db, student))
    db.flush()
    add_session(db, student)


def test_a_completed_game_also_frees_the_slot(db: Session) -> None:
    student = add_user(db)
    add_session(
        db,
        student,
        state=SessionState.COMPLETED,
        completed_at=datetime.now(UTC),
        current_position=5,
    )
    add_session(db, student)


def test_a_class_game_uses_the_class_language(db: Session) -> None:
    school_class = add_class(db, Language.JA)
    add_session(db, class_id=school_class.id, target_language=Language.JA)
    with pytest.raises(IntegrityError, match="fk_game_sessions_class_id_target_language_classes"):
        add_session(db, class_id=school_class.id, target_language=Language.FR)


def test_practice_games_can_use_any_language(db: Session) -> None:
    add_session(db, class_id=None, target_language=Language.KO)


@pytest.mark.parametrize("difficulty", [0, 4])
def test_difficulty_is_one_to_three(db: Session, difficulty: int) -> None:
    with pytest.raises(IntegrityError, match="ck_game_sessions_difficulty"):
        add_session(db, difficulty=difficulty)


def test_only_an_abandoned_game_has_an_end_reason(db: Session) -> None:
    with pytest.raises(IntegrityError, match="ck_game_sessions_end_reason_matches_state"):
        add_session(db, end_reason=SessionEndReason.QUIT)


def test_an_abandoned_game_needs_an_end_reason(db: Session) -> None:
    with pytest.raises(IntegrityError, match="ck_game_sessions_end_reason_matches_state"):
        add_session(db, state=SessionState.ABANDONED)


def test_paused_at_is_set_only_while_paused(db: Session) -> None:
    with pytest.raises(IntegrityError, match="ck_game_sessions_paused_at_matches_state"):
        add_session(db, state=SessionState.PAUSED)
    db.rollback()
    with pytest.raises(IntegrityError, match="ck_game_sessions_paused_at_matches_state"):
        add_session(db, paused_at=datetime.now(UTC))


def test_completed_at_is_set_only_when_completed(db: Session) -> None:
    with pytest.raises(IntegrityError, match="ck_game_sessions_completed_at_matches_state"):
        add_session(db, state=SessionState.COMPLETED)
    db.rollback()
    with pytest.raises(IntegrityError, match="ck_game_sessions_completed_at_matches_state"):
        add_session(db, completed_at=datetime.now(UTC))


def test_an_abandoned_game_cannot_keep_paused_at(db: Session) -> None:
    with pytest.raises(IntegrityError, match="ck_game_sessions_paused_at_matches_state"):
        add_session(
            db,
            state=SessionState.ABANDONED,
            end_reason=SessionEndReason.EXPIRED,
            paused_at=datetime.now(UTC),
        )


# the quoted name, so "end_reason" doesn't also match "end_reason_matches_state"
@pytest.mark.parametrize(
    ("fields", "constraint"),
    [
        ({"state": "finished"}, "ck_game_sessions_state"),
        ({"state": SessionState.ABANDONED, "end_reason": "bored"}, "ck_game_sessions_end_reason"),
        ({"target_language": "en"}, "ck_game_sessions_target_language"),
    ],
)
def test_unknown_values_are_rejected(db: Session, fields: dict[str, str], constraint: str) -> None:
    with pytest.raises(IntegrityError, match=f'"{constraint}"'):
        add_session(db, **fields)


@pytest.mark.parametrize(
    ("field", "value", "constraint"),
    [
        ("current_position", 0, "ck_game_sessions_current_position"),
        ("shuffles_used", -1, "ck_game_sessions_shuffles_used"),
        ("resume_count", -1, "ck_game_sessions_resume_count"),
        ("total_score", -1, "ck_game_sessions_total_score"),
    ],
)
def test_game_counters_stay_in_range(db: Session, field: str, value: int, constraint: str) -> None:
    with pytest.raises(IntegrityError, match=constraint):
        add_session(db, **{field: value})
