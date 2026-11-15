"""game_sessions and session_locations reject bad data at the database level."""

from collections.abc import Callable
from datetime import UTC, datetime

import pytest
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.models.accounts import User
from app.models.engagement import Class
from app.models.enums import Language, LocationStatus, SessionEndReason, SessionState, SkipReason
from app.models.game import GameSession, SessionLocation


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


# session_locations


def test_raw_sql_location_inserts_get_the_server_defaults(
    db: Session, make_game_session: Callable[..., GameSession]
) -> None:
    location = db.execute(
        text(
            "INSERT INTO session_locations (session_id, location_id, position, clue_id,"
            " audio_asset_id, accepted_answers_snapshot, gps_points_snapshot, radius_snapshot)"
            " VALUES (:session_id, gen_random_uuid(), 1, gen_random_uuid(), gen_random_uuid(),"
            ' \'["SCI333"]\', \'[{"lat": 49.9, "lng": -119.4}]\', 30)'
            " RETURNING status, attempts_used, points_awarded, flags"
        ),
        {"session_id": make_game_session().id},
    ).one()
    assert location.status == LocationStatus.PENDING
    assert (location.attempts_used, location.points_awarded) == (0, 0)
    assert location.flags == []


def test_a_position_is_used_once_per_game(
    make_game_session: Callable[..., GameSession],
    make_session_location: Callable[..., SessionLocation],
) -> None:
    game = make_game_session()
    make_session_location(game, 1)
    with pytest.raises(IntegrityError, match="uq_session_locations_session_id_position"):
        make_session_location(game, 1)


def test_a_shuffle_can_replace_the_row_at_a_position(
    make_game_session: Callable[..., GameSession],
    make_session_location: Callable[..., SessionLocation],
) -> None:
    # the unique position only counts rows that weren't shuffled, so a second shuffle can
    # replace the replacement, and its rows stay too
    game = make_game_session()
    for _ in range(2):
        make_session_location(
            game, 1, status=LocationStatus.SKIPPED, skip_reason=SkipReason.SHUFFLED
        )
    make_session_location(game, 1)
    with pytest.raises(IntegrityError, match="uq_session_locations_session_id_position"):
        make_session_location(
            game, 1, status=LocationStatus.SKIPPED, skip_reason=SkipReason.SESSION_ENDED
        )


def test_the_position_index_is_partial_in_the_model_and_the_database(db: Session) -> None:
    # alembic check doesn't compare an index's WHERE, so this is what catches the model and the
    # migration drifting apart
    name = "uq_session_locations_session_id_position"
    indexdef = db.scalar(
        text("SELECT indexdef FROM pg_indexes WHERE indexname = :name"), {"name": name}
    )
    assert indexdef is not None
    assert indexdef.startswith(f"CREATE UNIQUE INDEX {name} ON public.session_locations")
    assert indexdef.endswith("WHERE (skip_reason IS DISTINCT FROM 'shuffled'::text)")
    index = next(index for index in SessionLocation.__table__.indexes if index.name == name)
    assert index.unique
    assert [column.name for column in index.columns] == ["session_id", "position"]
    where = index.dialect_options["postgresql"]["where"]
    assert str(where) == "skip_reason IS DISTINCT FROM 'shuffled'"


def test_a_location_is_drawn_once_per_game(
    make_game_session: Callable[..., GameSession],
    make_session_location: Callable[..., SessionLocation],
) -> None:
    game = make_game_session()
    first = make_session_location(game, 1)
    with pytest.raises(IntegrityError, match="uq_session_locations_session_id_location_id"):
        make_session_location(game, 2, location_id=first.location_id)


@pytest.mark.parametrize(
    ("status", "skip_reason"),
    [(LocationStatus.SKIPPED, None), (LocationStatus.PENDING, SkipReason.SESSION_ENDED)],
)
def test_only_a_skipped_location_has_a_skip_reason(
    make_game_session: Callable[..., GameSession],
    make_session_location: Callable[..., SessionLocation],
    status: str,
    skip_reason: str | None,
) -> None:
    with pytest.raises(IntegrityError, match="ck_session_locations_skip_reason_matches_status"):
        make_session_location(make_game_session(), 1, status=status, skip_reason=skip_reason)


def test_a_deactivated_location_is_skipped(
    db: Session,
    make_game_session: Callable[..., GameSession],
    make_session_location: Callable[..., SessionLocation],
) -> None:
    location = make_session_location(
        make_game_session(), 1, status=LocationStatus.SKIPPED, skip_reason=SkipReason.DEACTIVATED
    )
    db.refresh(location)
    assert (location.status, location.skip_reason) == ("skipped", "deactivated")
    assert (location.points_awarded, location.completed_at) == (0, None)


@pytest.mark.parametrize(
    ("field", "value", "constraint"),
    [
        ("position", 0, "ck_session_locations_position"),
        ("status", "lost", "ck_session_locations_status"),
        ("attempts_used", -1, "ck_session_locations_attempts_used"),
        ("points_awarded", 50, "ck_session_locations_points_awarded"),
        ("radius_snapshot", 0, "ck_session_locations_radius_snapshot"),
        ("last_lat", 91.0, "ck_session_locations_last_lat"),
        ("last_lng", -181.0, "ck_session_locations_last_lng"),
        ("last_accuracy_m", -1.0, "ck_session_locations_last_accuracy_m"),
        ("arrival_override", "manual", "ck_session_locations_arrival_override"),
    ],
)
def test_location_values_stay_in_range(
    make_game_session: Callable[..., GameSession],
    make_session_location: Callable[..., SessionLocation],
    field: str,
    value: object,
    constraint: str,
) -> None:
    fields = {"position": 1, field: value}
    # the quotes stop "status" from also matching other constraint names
    with pytest.raises(IntegrityError, match=f'"{constraint}"'):
        make_session_location(make_game_session(), **fields)


def test_unknown_skip_reason_is_rejected(
    make_game_session: Callable[..., GameSession],
    make_session_location: Callable[..., SessionLocation],
) -> None:
    with pytest.raises(IntegrityError, match='"ck_session_locations_skip_reason"'):
        make_session_location(
            make_game_session(), 1, status=LocationStatus.SKIPPED, skip_reason="bored"
        )


def test_a_completed_location_has_its_points_and_time(
    db: Session,
    make_game_session: Callable[..., GameSession],
    make_session_location: Callable[..., SessionLocation],
) -> None:
    location = make_session_location(
        make_game_session(),
        1,
        status=LocationStatus.COMPLETED,
        points_awarded=100,
        completed_at=datetime.now(UTC),
        attempts_used=6,
    )
    db.refresh(location)
    assert (location.status, location.points_awarded, location.attempts_used) == (
        "completed",
        100,
        6,
    )
    assert location.completed_at is not None


NOW = datetime.now(UTC)
POINTS, TIME = "points_match_status", "completed_at_matches_status"
SKIPPED = {"status": LocationStatus.SKIPPED, "skip_reason": SkipReason.SESSION_ENDED}


@pytest.mark.parametrize(
    ("fields", "constraint"),
    [
        ({"status": LocationStatus.COMPLETED, "completed_at": NOW}, POINTS),  # no points
        ({"status": LocationStatus.FAILED, "points_awarded": 100}, POINTS),
        (SKIPPED | {"points_awarded": 100}, POINTS),
        ({"status": LocationStatus.COMPLETED, "points_awarded": 100}, TIME),  # no completed_at
        ({"status": LocationStatus.FAILED, "completed_at": NOW}, TIME),
    ],
)
def test_points_and_completed_at_match_the_status(
    make_game_session: Callable[..., GameSession],
    make_session_location: Callable[..., SessionLocation],
    fields: dict[str, object],
    constraint: str,
) -> None:
    with pytest.raises(IntegrityError, match=f"ck_session_locations_{constraint}"):
        make_session_location(make_game_session(), 1, **fields)


@pytest.mark.parametrize("field", ["accepted_answers_snapshot", "gps_points_snapshot"])
@pytest.mark.parametrize("value", [[], None, {"lat": 49.9, "lng": -119.4}])
def test_a_snapshot_needs_at_least_one_answer_and_entrance(
    make_game_session: Callable[..., GameSession],
    make_session_location: Callable[..., SessionLocation],
    field: str,
    value: object,
) -> None:
    """[] or JSON null would give a location that can never be completed."""
    with pytest.raises(IntegrityError, match=f'"ck_session_locations_{field}"'):
        make_session_location(make_game_session(), 1, **{field: value})


def test_appending_a_flag_is_saved(
    db: Session,
    make_game_session: Callable[..., GameSession],
    make_session_location: Callable[..., SessionLocation],
) -> None:
    location = make_session_location(make_game_session(), 1)
    location.flags.append("speed_anomaly")
    db.flush()
    db.expire(location)
    assert location.flags == ["speed_anomaly"]
