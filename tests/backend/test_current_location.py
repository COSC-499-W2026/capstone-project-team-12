"""GET /games/{id}/current-location (A19): the current location, skipping deactivated ones.

Content is still a stub: its eligible locations are active, and any other id (e.g. a random one
from make_session_location) counts as deactivated.
"""

from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from typing import Any
from urllib.parse import urlsplit
from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.content import interface as content
from app.errors import AppError, ErrorCode
from app.game.current_location import CurrentLocation, Stage, get_current_location
from app.game.progress import current_row, move_on
from app.game.shuffle import shuffle_game
from app.game.status import GameStatus
from app.models.accounts import User
from app.models.enums import Difficulty, Language, LocationStatus, SessionState, SkipReason
from app.models.game import GameSession, SessionLocation
from app.services.audio import UploadedAudioService

CLOCK = ("app.game.current_location", "app.game.status", "app.game.control")
NOW = datetime(2026, 10, 29, 20, 0, tzinfo=UTC)


@pytest.fixture
def active(db: Session) -> list[UUID]:
    return sorted(content.eligible_location_ids(Language.JA, Difficulty.BASIC, db))


@pytest.fixture
def game_with(
    make_game_session: Callable[..., GameSession],
    make_session_location: Callable[..., SessionLocation],
    freeze_clock: Callable[..., None],
) -> Callable[..., tuple[GameSession, list[SessionLocation]]]:
    """A game at `current`, with one row per (status, location id) in position order."""

    def make(
        rows: list[tuple[LocationStatus, UUID]], current: int = 1, **fields: Any
    ) -> tuple[GameSession, list[SessionLocation]]:
        freeze_clock(NOW, *CLOCK)
        game = make_game_session(
            current_position=current, last_activity_at=NOW - timedelta(hours=1), **fields
        )
        made = []
        for position, (status, location_id) in enumerate(rows, start=1):
            extra: dict[str, Any] = {}
            if status == LocationStatus.NAVIGATING:
                extra["started_at"] = NOW - timedelta(minutes=1)
            if status == LocationStatus.COMPLETED:
                extra |= {"points_awarded": 100, "completed_at": NOW}
            made.append(
                make_session_location(
                    game, position, status=status, location_id=location_id, **extra
                )
            )
        return game, made

    return make


N, P, V = LocationStatus.NAVIGATING, LocationStatus.PENDING, LocationStatus.VERIFYING


@pytest.mark.parametrize("paused", [False, True])
def test_stage_1(
    db: Session, game_with: Callable[..., Any], active: list[UUID], paused: bool
) -> None:
    fields = {"state": SessionState.PAUSED, "paused_at": NOW} if paused else {}
    game, rows = game_with([(N, active[0]), (P, active[1])], **fields)

    found = get_current_location(game.student_id, game.id, db)

    assert isinstance(found, CurrentLocation)
    assert (found.position, found.stage, found.attempts_used, found.attempts_left) == (
        1,
        Stage.NAVIGATION,
        0,
        6,
    )
    assert found.state == (SessionState.PAUSED if paused else SessionState.ACTIVE)
    # 3 minutes after the location became current, paused time included
    assert found.gps_override_available_at == NOW + timedelta(minutes=2)
    expected = UploadedAudioService().clue_audio_url(rows[0].audio_asset_id, db)
    assert urlsplit(found.clue_audio_url).path == urlsplit(expected).path
    # reading doesn't count as playing
    assert game.last_activity_at == NOW - timedelta(hours=1)


def test_stage_2_even_if_deactivated(db: Session, game_with: Callable[..., Any]) -> None:
    # a location already reached isn't skipped: the student can finish the photo
    game, rows = game_with([(V, uuid4()), (P, uuid4())])
    rows[0].attempts_used = 2
    db.flush()

    found = get_current_location(game.student_id, game.id, db)

    assert isinstance(found, CurrentLocation)
    assert (found.stage, found.attempts_left, found.gps_override_available_at) == (
        Stage.VERIFICATION,
        4,
        None,
    )
    assert rows[0].status == LocationStatus.VERIFYING


def test_a_row_replaced_by_a_shuffle_is_ignored(
    db: Session,
    game_with: Callable[..., Any],
    make_session_location: Callable[..., SessionLocation],
    active: list[UUID],
) -> None:
    game, rows = game_with([(N, active[0])])
    # an inactive location that was shuffled away at the same position
    make_session_location(
        game, 1, status=LocationStatus.SKIPPED, skip_reason=SkipReason.SHUFFLED, attempts_used=3
    )

    found = get_current_location(game.student_id, game.id, db)

    assert isinstance(found, CurrentLocation)
    assert (found.position, found.attempts_used) == (1, 0)
    assert rows[0].status == LocationStatus.NAVIGATING


def test_move_on_ignores_rows_a_shuffle_replaced(
    db: Session, game_with: Callable[..., Any], active: list[UUID]
) -> None:
    game, rows = game_with([(N, active[0]), (P, active[1]), (LocationStatus.COMPLETED, active[2])])
    replaced, later = rows[:2], rows[2]
    shuffle_game(game.student_id, game.id, 0, db)
    current = current_row(game, db)
    current.location_id = uuid4()
    db.flush()

    found = get_current_location(game.student_id, game.id, db)
    landed = current_row(game, db)

    assert isinstance(found, CurrentLocation)
    assert landed.id not in {row.id for row in replaced}
    assert (found.position, landed.status, game.current_position) == (2, N, 2)
    assert [(row.status, row.skip_reason) for row in replaced] == [
        (LocationStatus.SKIPPED, SkipReason.SHUFFLED)
    ] * 2
    landed.status = LocationStatus.COMPLETED
    landed.points_awarded, landed.completed_at = 100, NOW
    move_on(game, NOW, db)
    assert game.state == SessionState.COMPLETED
    assert (later.status, later.skip_reason, later.points_awarded) == (
        LocationStatus.COMPLETED,
        None,
        100,
    )


def test_deactivated_locations_are_skipped_until_an_active_one(
    db: Session, game_with: Callable[..., Any], active: list[UUID]
) -> None:
    game, rows = game_with([(N, uuid4()), (P, uuid4()), (P, active[0]), (P, uuid4())])

    found = get_current_location(game.student_id, game.id, db)

    assert isinstance(found, CurrentLocation)
    assert (found.position, found.stage) == (3, Stage.NAVIGATION)
    assert [(r.status, r.skip_reason) for r in rows] == [
        (LocationStatus.SKIPPED, SkipReason.DEACTIVATED),
        (LocationStatus.SKIPPED, SkipReason.DEACTIVATED),
        (LocationStatus.NAVIGATING, None),
        (LocationStatus.PENDING, None),
    ]
    assert (game.current_position, rows[2].started_at, game.last_activity_at) == (3, NOW, NOW)
    assert found.gps_override_available_at == NOW + timedelta(minutes=3)


def test_a_deactivated_last_location_completes_a_paused_game(
    db: Session, game_with: Callable[..., Any], active: list[UUID]
) -> None:
    game, rows = game_with(
        [(LocationStatus.COMPLETED, active[0]), (N, uuid4())],
        current=2,
        state=SessionState.PAUSED,
        paused_at=NOW,
    )

    found = get_current_location(game.student_id, game.id, db)

    assert isinstance(found, GameStatus)
    assert (found.state, found.paused_at) == (SessionState.COMPLETED, None)
    assert (game.completed_at, rows[1].skip_reason) == (NOW, SkipReason.DEACTIVATED)


@pytest.mark.parametrize("status", [None, LocationStatus.COMPLETED])
def test_a_game_out_of_step_is_409_not_500(
    db: Session, game_with: Callable[..., Any], active: list[UUID], status: LocationStatus | None
) -> None:
    rows = [] if status is None else [(status, active[0])]
    game, _ = game_with(rows)

    with pytest.raises(AppError) as error:
        get_current_location(game.student_id, game.id, db)

    assert error.value.code == ErrorCode.INVALID_STAGE


def test_the_route(
    client: TestClient,
    auth_headers: Callable[[User], dict[str, str]],
    make_user: Callable[..., User],
    game_with: Callable[..., Any],
    active: list[UUID],
    transaction_calls: list[str],
    assert_error: Callable[..., None],
) -> None:
    student = make_user()
    game, _ = game_with([(N, active[0])], student=student)
    url = f"/api/v1/games/{game.id}/current-location"
    transaction_calls.clear()

    response = client.get(url, headers=auth_headers(student))

    assert response.status_code == 200, response.text
    assert set(response.json()) == {
        "session_id",
        "state",
        "position",
        "stage",
        "clue_audio_url",
        "attempts_used",
        "attempts_left",
        "gps_override_available_at",
    }
    assert transaction_calls.count("commit") == 1
    assert "SCI333" not in response.text and "lat" not in response.text
    assert_error(client.get(url, headers=auth_headers(make_user())), 404, ErrorCode.NOT_FOUND)
    admin = make_user(role="admin")
    assert_error(client.get(url, headers=auth_headers(admin)), 403, ErrorCode.FORBIDDEN_ROLE)
    assert_error(client.get(url), 401, ErrorCode.NOT_AUTHENTICATED)


def test_the_route_returns_the_status_when_the_game_completes(
    db: Session,
    client: TestClient,
    auth_headers: Callable[[User], dict[str, str]],
    make_user: Callable[..., User],
    game_with: Callable[..., Any],
) -> None:
    student = make_user()
    game, _ = game_with([(N, uuid4())], student=student)

    response = client.get(
        f"/api/v1/games/{game.id}/current-location", headers=auth_headers(student)
    )

    assert response.status_code == 200, response.text
    assert (response.json()["state"], response.json()["locations"][0]["skip_reason"]) == (
        SessionState.COMPLETED.value,
        SkipReason.DEACTIVATED.value,
    )
    db.refresh(game)
    assert game.state == SessionState.COMPLETED


def test_three_weak_pings_allow_the_override_from_the_last_ping(
    db: Session, game_with: Callable[..., Any], active: list[UUID]
) -> None:
    game, rows = game_with([(N, active[0])])
    rows[0].weak_pings_in_row, rows[0].last_ping_at = 3, NOW - timedelta(seconds=10)
    db.flush()

    found = get_current_location(game.student_id, game.id, db)

    assert isinstance(found, CurrentLocation)
    assert found.gps_override_available_at == NOW - timedelta(seconds=10)
