"""POST /games/{id}/clue-plays (#436): each play or replay of the current clue is counted."""

from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from functools import partial
from typing import Any
from uuid import UUID

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.errors import AppError, ErrorCode
from app.game.clue_plays import record_clue_play
from app.game.control import quit_game
from app.game.progress import move_on
from app.game.shuffle import shuffle_game
from app.models.accounts import User
from app.models.enums import Difficulty, Language, LocationStatus, SessionState
from app.models.game import GameSession, SessionLocation

NOW = datetime(2026, 11, 22, 20, 0, tzinfo=UTC)
AN_HOUR_AGO = NOW - timedelta(hours=1)


@pytest.fixture
def playing(
    navigating_game: Callable[..., Any], playable_location_ids: Callable[..., list[UUID]]
) -> Callable[..., Any]:
    """An active game at its first location, a playable one; the last action was an hour ago."""
    location_id = playable_location_ids(Language.JA, Difficulty.BASIC)[0]
    return partial(navigating_game, NOW, "app.game.clue_plays", location_id=location_id)


def record(game: GameSession, db: Session, position: int = 1) -> int:
    return record_clue_play(game.student_id, game.id, position, db)


def test_each_play_adds_one_and_counts_as_a_game_action(
    db: Session, playing: Callable[..., Any]
) -> None:
    game, row = playing()

    counts = [record(game, db) for _ in range(3)]

    assert counts == [1, 2, 3]
    assert (row.clue_plays, game.last_activity_at) == (3, NOW)


def test_plays_are_kept_per_location(
    db: Session,
    playing: Callable[..., Any],
    make_session_location: Callable[..., SessionLocation],
    playable_location_ids: Callable[..., list[UUID]],
) -> None:
    game, first = playing()
    second = make_session_location(
        game, 2, location_id=playable_location_ids(Language.JA, Difficulty.BASIC)[1]
    )
    record(game, db)
    record(game, db)
    first.status, first.completed_at, first.points_awarded = LocationStatus.COMPLETED, NOW, 100
    move_on(game, NOW, db)

    assert second.clue_plays == 0
    assert record(game, db, position=2) == 1
    assert (first.clue_plays, second.clue_plays) == (2, 1)


def test_a_shuffled_in_location_starts_at_0(
    db: Session, playing: Callable[..., Any], freeze_clock: Callable[..., None]
) -> None:
    game, old = playing()
    record(game, db)
    freeze_clock(NOW, "app.game.shuffle")
    shuffle_game(game.student_id, game.id, game.shuffles_used, db)
    new = db.scalars(
        select(SessionLocation).where(
            SessionLocation.session_id == game.id,
            SessionLocation.position == 1,
            SessionLocation.status == LocationStatus.NAVIGATING,
        )
    ).one()

    assert new.clue_plays == 0
    assert record(game, db) == 1
    assert old.clue_plays == 1


@pytest.mark.parametrize(
    ("game_fields", "position", "code"),
    [
        ({}, 2, ErrorCode.STATE_CHANGED),
        ({"state": SessionState.PAUSED, "paused_at": AN_HOUR_AGO}, 1, ErrorCode.SESSION_NOT_ACTIVE),
    ],
)
def test_refused_without_changes(
    db: Session,
    playing: Callable[..., Any],
    game_fields: dict[str, Any],
    position: int,
    code: ErrorCode,
) -> None:
    game, row = playing(game=game_fields)

    with pytest.raises(AppError) as error:
        record(game, db, position)

    assert error.value.code == code
    assert (row.clue_plays, game.last_activity_at) == (0, AN_HOUR_AGO)


def test_an_ended_game_is_409(db: Session, playing: Callable[..., Any]) -> None:
    game, row = playing()
    quit_game(game.student_id, game.id, db)

    with pytest.raises(AppError) as error:
        record(game, db)

    assert error.value.code == ErrorCode.SESSION_NOT_ACTIVE
    assert row.clue_plays == 0


def test_the_route(
    db: Session,
    client: TestClient,
    auth_headers: Callable[[User], dict[str, str]],
    make_user: Callable[..., User],
    playing: Callable[..., Any],
    transaction_calls: list[str],
    assert_error: Callable[..., None],
) -> None:
    game, row = playing()
    # saved already, so a rolled-back 409 does not remove the account
    db.commit()
    student = db.get(User, game.student_id)
    assert student is not None
    url = f"/api/v1/games/{game.id}/clue-plays"
    headers = auth_headers(student)
    assert_error(client.post(url, json={}, headers=headers), 422, ErrorCode.VALIDATION_ERROR)
    stale = client.post(url, json={"position": 2}, headers=headers)
    assert_error(stale, 409, ErrorCode.STATE_CHANGED)
    transaction_calls.clear()

    response = client.post(url, json={"position": 1}, headers=headers)

    assert response.status_code == 200, response.text
    assert response.json() == {"position": 1, "clue_plays": 1}
    assert transaction_calls.count("commit") == 1
    db.refresh(row)
    assert row.clue_plays == 1
    other = auth_headers(make_user())
    assert_error(client.post(url, json={"position": 1}, headers=other), 404, ErrorCode.NOT_FOUND)
    admin = auth_headers(make_user(role="admin"))
    assert_error(
        client.post(url, json={"position": 1}, headers=admin), 403, ErrorCode.FORBIDDEN_ROLE
    )
    assert_error(client.post(url, json={"position": 1}), 401, ErrorCode.NOT_AUTHENTICATED)
