"""game_sessions rejects bad data at the database level."""

from collections.abc import Callable
from datetime import UTC, datetime

import pytest
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.models.accounts import User
from app.models.engagement import Class
from app.models.enums import Language, SessionEndReason, SessionState
from app.models.game import GameSession


def end(game: GameSession, reason: str = SessionEndReason.QUIT) -> None:
    game.state = SessionState.ABANDONED
    game.end_reason = reason


# game_sessions


def test_raw_sql_inserts_get_the_server_defaults(
    db: Session, make_user: Callable[..., User]
) -> None:
    student = make_user()
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


def test_a_student_has_only_one_open_game(
    make_user: Callable[..., User], make_game_session: Callable[..., GameSession]
) -> None:
    student = make_user()
    make_game_session(student)
    with pytest.raises(IntegrityError, match="uq_game_sessions_one_open_per_student"):
        make_game_session(student)


def test_a_paused_game_also_counts_as_open(
    make_user: Callable[..., User], make_game_session: Callable[..., GameSession]
) -> None:
    student = make_user()
    make_game_session(student, state=SessionState.PAUSED, paused_at=datetime.now(UTC))
    with pytest.raises(IntegrityError, match="uq_game_sessions_one_open_per_student"):
        make_game_session(student)


def test_a_new_game_can_start_after_the_old_one_ended(
    db: Session, make_user: Callable[..., User], make_game_session: Callable[..., GameSession]
) -> None:
    student = make_user()
    end(make_game_session(student))
    db.flush()
    make_game_session(student)


def test_a_completed_game_also_frees_the_slot(
    make_user: Callable[..., User], make_game_session: Callable[..., GameSession]
) -> None:
    student = make_user()
    make_game_session(
        student,
        state=SessionState.COMPLETED,
        completed_at=datetime.now(UTC),
        current_position=5,
    )
    make_game_session(student)


def test_a_class_game_uses_the_class_language(
    make_class: Callable[..., Class], make_game_session: Callable[..., GameSession]
) -> None:
    school_class = make_class(target_language=Language.JA)
    make_game_session(class_id=school_class.id, target_language=Language.JA)
    with pytest.raises(IntegrityError, match="fk_game_sessions_class_id_target_language_classes"):
        make_game_session(class_id=school_class.id, target_language=Language.FR)


def test_practice_games_can_use_any_language(make_game_session: Callable[..., GameSession]) -> None:
    make_game_session(class_id=None, target_language=Language.KO)


@pytest.mark.parametrize("difficulty", [0, 4])
def test_difficulty_is_one_to_three(
    make_game_session: Callable[..., GameSession], difficulty: int
) -> None:
    with pytest.raises(IntegrityError, match="ck_game_sessions_difficulty"):
        make_game_session(difficulty=difficulty)


def test_only_an_abandoned_game_has_an_end_reason(
    make_game_session: Callable[..., GameSession],
) -> None:
    with pytest.raises(IntegrityError, match="ck_game_sessions_end_reason_matches_state"):
        make_game_session(end_reason=SessionEndReason.QUIT)


def test_an_abandoned_game_needs_an_end_reason(
    make_game_session: Callable[..., GameSession],
) -> None:
    with pytest.raises(IntegrityError, match="ck_game_sessions_end_reason_matches_state"):
        make_game_session(state=SessionState.ABANDONED)


def test_paused_at_is_set_only_while_paused(
    db: Session, make_game_session: Callable[..., GameSession]
) -> None:
    with pytest.raises(IntegrityError, match="ck_game_sessions_paused_at_matches_state"):
        make_game_session(state=SessionState.PAUSED)
    db.rollback()
    with pytest.raises(IntegrityError, match="ck_game_sessions_paused_at_matches_state"):
        make_game_session(paused_at=datetime.now(UTC))


def test_completed_at_is_set_only_when_completed(
    db: Session, make_game_session: Callable[..., GameSession]
) -> None:
    with pytest.raises(IntegrityError, match="ck_game_sessions_completed_at_matches_state"):
        make_game_session(state=SessionState.COMPLETED)
    db.rollback()
    with pytest.raises(IntegrityError, match="ck_game_sessions_completed_at_matches_state"):
        make_game_session(completed_at=datetime.now(UTC))


def test_an_abandoned_game_cannot_keep_paused_at(
    make_game_session: Callable[..., GameSession],
) -> None:
    with pytest.raises(IntegrityError, match="ck_game_sessions_paused_at_matches_state"):
        make_game_session(
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
def test_unknown_values_are_rejected(
    make_game_session: Callable[..., GameSession], fields: dict[str, str], constraint: str
) -> None:
    with pytest.raises(IntegrityError, match=f'"{constraint}"'):
        make_game_session(**fields)


@pytest.mark.parametrize(
    ("field", "value", "constraint"),
    [
        ("current_position", 0, "ck_game_sessions_current_position"),
        ("shuffles_used", -1, "ck_game_sessions_shuffles_used"),
        ("resume_count", -1, "ck_game_sessions_resume_count"),
        ("total_score", -1, "ck_game_sessions_total_score"),
    ],
)
def test_game_counters_stay_in_range(
    make_game_session: Callable[..., GameSession], field: str, value: int, constraint: str
) -> None:
    with pytest.raises(IntegrityError, match=constraint):
        make_game_session(**{field: value})
