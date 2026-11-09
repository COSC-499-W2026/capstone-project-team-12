"""shuffle_game (A9): replace the unfinished locations, keep the finished ones and their rows."""

from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from urllib.parse import urlsplit
from uuid import UUID, uuid4

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.content import interface as content
from app.errors import AppError, ErrorCode
from app.game import shuffle
from app.game.control import quit_game
from app.game.draw import draw_locations
from app.game.sessions import session_idle_limit
from app.game.shuffle import shuffle_game
from app.models.accounts import User
from app.models.enums import (
    Difficulty,
    Language,
    LocationStatus,
    SessionEndReason,
    SessionState,
    SkipReason,
)
from app.models.game import GameSession, SessionLocation
from app.services.audio import MediaNotFoundError, UploadedAudioService

# the modules whose clock shuffle reads
CLOCK = ("app.game.shuffle", "app.game.control", "app.game.status")


class _WithoutRecordings(UploadedAudioService):
    def clue_audio_url(self, audio_asset_id: UUID, db: Session) -> str:
        raise MediaNotFoundError


@pytest.fixture
def eligible(db: Session) -> list[UUID]:
    return sorted(content.eligible_location_ids(Language.JA, Difficulty.BASIC, db))


@pytest.fixture
def game_at_three(
    make_game_session: Callable[..., GameSession],
    make_session_location: Callable[..., SessionLocation],
    eligible: list[UUID],
) -> Callable[..., GameSession]:
    """A game at position 3: 1 completed, 2 failed, 3 being verified, 4 and 5 still to come.
    Its locations are the first five eligible ones."""

    def make(**fields: object) -> GameSession:
        now = datetime.now(UTC)
        game = make_game_session(current_position=3, total_score=100, **fields)
        rows = [
            {"status": LocationStatus.COMPLETED, "points_awarded": 100, "completed_at": now},
            {"status": LocationStatus.FAILED, "attempts_used": 6},
            {"status": LocationStatus.VERIFYING, "attempts_used": 2, "started_at": now},
            {},
            {},
        ]
        for position, row in enumerate(rows, start=1):
            make_session_location(game, position, location_id=eligible[position - 1], **row)
        return game

    return make


def _rows(game: GameSession, db: Session) -> list[SessionLocation]:
    return list(
        db.scalars(
            select(SessionLocation)
            .where(SessionLocation.session_id == game.id)
            .order_by(SessionLocation.position, SessionLocation.started_at.nulls_first())
            .execution_options(populate_existing=True)
        )
    )


def _live(game: GameSession, db: Session) -> list[SessionLocation]:
    return [row for row in _rows(game, db) if row.skip_reason != SkipReason.SHUFFLED]


def test_shuffle_replaces_only_unfinished_locations(
    db: Session,
    game_at_three: Callable[..., GameSession],
    eligible: list[UUID],
    freeze_clock: Callable[..., None],
) -> None:
    # Proposal Test Case 2: finished locations and the points already earned are kept
    game = game_at_three()
    now = datetime.now(UTC).replace(microsecond=0)
    freeze_clock(now, *CLOCK)

    shuffled = shuffle_game(game.student_id, game.id, 0, db)

    assert (game.shuffles_used, game.last_activity_at, game.total_score) == (1, now, 100)
    assert (game.state, game.current_position) == (SessionState.ACTIVE, 3)
    rows = _rows(game, db)
    replaced = [row for row in rows if row.skip_reason == SkipReason.SHUFFLED]
    # the replaced rows stay, with the attempts already made
    assert [(row.position, row.location_id, row.attempts_used) for row in replaced] == [
        (3, eligible[2], 2),
        (4, eligible[3], 0),
        (5, eligible[4], 0),
    ]
    assert all(row.status == LocationStatus.SKIPPED for row in replaced)
    live = _live(game, db)
    assert [(row.position, row.status) for row in live] == [
        (1, LocationStatus.COMPLETED),
        (2, LocationStatus.FAILED),
        (3, LocationStatus.NAVIGATING),
        (4, LocationStatus.PENDING),
        (5, LocationStatus.PENDING),
    ]
    assert [row.location_id for row in live[:2]] == eligible[:2]
    assert live[0].points_awarded == 100
    new = live[2:]
    assert not {row.location_id for row in new} & set(eligible[:5])
    assert (new[0].started_at, new[0].attempts_used) == (now, 0)
    assert [row.started_at for row in new[1:]] == [None, None]

    status = shuffled.status
    assert status.shuffles_used == 1
    assert [(loc.position, loc.status) for loc in status.locations] == [
        (row.position, row.status) for row in live
    ]
    expected = UploadedAudioService().clue_audio_url(new[0].audio_asset_id, db)
    assert urlsplit(shuffled.clue_audio_url).path == urlsplit(expected).path


def test_a_second_shuffle_never_brings_back_a_replaced_location(
    db: Session,
    game_at_three: Callable[..., GameSession],
    eligible: list[UUID],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    game = game_at_three()
    shuffle_game(game.student_id, game.id, 0, db)
    first_draw = [row.location_id for row in _live(game, db)[2:]]
    # a random draw would now try the replaced locations first
    replaced_ids = set(eligible[2:5]) | set(first_draw)
    monkeypatch.setattr(
        "app.game.draw.random.shuffle", lambda ids: ids.sort(key=lambda i: i not in replaced_ids)
    )

    shuffle_game(game.student_id, game.id, 1, db)

    live = _live(game, db)
    assert not {row.location_id for row in live[2:]} & replaced_ids
    assert len(_rows(game, db)) == 11
    assert game.shuffles_used == 2


def test_a_stale_shuffles_used_changes_nothing(
    db: Session, game_at_three: Callable[..., GameSession]
) -> None:
    game = game_at_three()
    shuffle_game(game.student_id, game.id, 0, db)
    rows_before = [(row.id, row.status) for row in _rows(game, db)]

    # e.g. the app retries the request that already shuffled
    with pytest.raises(AppError) as error:
        shuffle_game(game.student_id, game.id, 0, db)

    assert error.value.code == ErrorCode.STATE_CHANGED
    assert error.value.details == [
        {
            "session_id": str(game.id),
            "state": SessionState.ACTIVE,
            "shuffles_used": 1,
            "current_position": 3,
        }
    ]
    assert game.shuffles_used == 1
    assert [(row.id, row.status) for row in _rows(game, db)] == rows_before


def test_a_third_shuffle_is_refused(db: Session, game_at_three: Callable[..., GameSession]) -> None:
    # Proposal Test Case 4 (shuffle part)
    game = game_at_three(shuffles_used=2)

    with pytest.raises(AppError) as error:
        shuffle_game(game.student_id, game.id, 2, db)

    assert error.value.code == ErrorCode.SHUFFLE_LIMIT_REACHED
    assert len(_rows(game, db)) == 5


def test_a_retry_of_the_last_shuffle_is_state_changed_not_the_limit(
    db: Session, game_at_three: Callable[..., GameSession]
) -> None:
    # the second shuffle went through but the app didn't get the answer and sends it again
    game = game_at_three(shuffles_used=2)

    with pytest.raises(AppError) as error:
        shuffle_game(game.student_id, game.id, 1, db)

    assert error.value.code == ErrorCode.STATE_CHANGED


def test_a_game_whose_current_location_is_finished_is_refused_not_a_500(
    db: Session, game_at_three: Callable[..., GameSession]
) -> None:
    # out of step: position 3 finished but the game never moved on
    game = game_at_three()
    current = _rows(game, db)[2]
    current.status = LocationStatus.FAILED
    db.flush()

    with pytest.raises(AppError) as error:
        shuffle_game(game.student_id, game.id, 0, db)

    assert error.value.code == ErrorCode.STATE_CHANGED
    assert game.shuffles_used == 0
    assert [row.skip_reason for row in _rows(game, db)] == [None] * 5


def test_a_paused_game_is_not_shuffled(
    db: Session, game_at_three: Callable[..., GameSession]
) -> None:
    paused_at = datetime.now(UTC) - timedelta(hours=1)
    game = game_at_three(state=SessionState.PAUSED, paused_at=paused_at, last_activity_at=paused_at)

    with pytest.raises(AppError) as error:
        shuffle_game(game.student_id, game.id, 0, db)

    assert error.value.code == ErrorCode.SESSION_NOT_ACTIVE
    assert error.value.details == [{"session_id": str(game.id), "state": SessionState.PAUSED}]
    # not used up, and still paused
    assert (game.shuffles_used, game.state, game.last_activity_at) == (
        0,
        SessionState.PAUSED,
        paused_at,
    )
    assert len(_rows(game, db)) == 5


def test_not_enough_new_locations_leaves_the_game_as_it_is(
    db: Session,
    game_at_three: Callable[..., GameSession],
    eligible: list[UUID],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    game = game_at_three()
    # the game's five plus two new ones, and the shuffle needs three
    monkeypatch.setattr(content, "eligible_location_ids", lambda *_: frozenset(eligible[:7]))

    with pytest.raises(AppError) as error:
        shuffle_game(game.student_id, game.id, 0, db)

    assert error.value.code == ErrorCode.NOT_ENOUGH_CONTENT
    assert game.shuffles_used == 0
    assert [row.status for row in _rows(game, db)][2:] == [
        LocationStatus.VERIFYING,
        LocationStatus.PENDING,
        LocationStatus.PENDING,
    ]


def test_a_missing_recording_leaves_the_game_as_it_is(
    db: Session, game_at_three: Callable[..., GameSession], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(shuffle, "get_audio_service", _WithoutRecordings)
    game = game_at_three()

    with pytest.raises(MediaNotFoundError):
        shuffle_game(game.student_id, game.id, 0, db)

    assert game.shuffles_used == 0
    assert len(_rows(game, db)) == 5


@pytest.mark.parametrize(
    "fields",
    [
        {"state": SessionState.COMPLETED, "completed_at": datetime.now(UTC)},
        {"state": SessionState.ABANDONED, "end_reason": SessionEndReason.QUIT},
    ],
)
def test_a_finished_game_is_not_active(
    db: Session, make_game_session: Callable[..., GameSession], fields: dict[str, object]
) -> None:
    game = make_game_session(**fields)

    with pytest.raises(AppError) as error:
        shuffle_game(game.student_id, game.id, 0, db)

    assert error.value.code == ErrorCode.SESSION_NOT_ACTIVE


def test_another_students_game_or_an_unknown_id_is_not_found(
    db: Session, make_user: Callable[..., User], game_at_three: Callable[..., GameSession]
) -> None:
    game = game_at_three()

    for student_id, game_id in ((make_user().id, game.id), (game.student_id, uuid4())):
        with pytest.raises(AppError) as error:
            shuffle_game(student_id, game_id, 0, db)
        assert error.value.code == ErrorCode.NOT_FOUND
    assert game.shuffles_used == 0


def test_a_game_idle_for_the_limit_is_expired_not_shuffled(
    db: Session, game_at_three: Callable[..., GameSession], freeze_clock: Callable[..., None]
) -> None:
    now, limit = datetime.now(UTC), session_idle_limit()
    freeze_clock(now, *CLOCK)
    game = game_at_three(last_activity_at=now - limit)

    with pytest.raises(AppError) as error:
        shuffle_game(game.student_id, game.id, 0, db)

    assert error.value.code == ErrorCode.SESSION_NOT_ACTIVE
    assert (game.end_reason, game.shuffles_used) == (SessionEndReason.EXPIRED, 0)


def test_a_quit_after_a_shuffle_carries_over_only_the_new_locations(
    db: Session,
    game_at_three: Callable[..., GameSession],
    eligible: list[UUID],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    game = game_at_three()
    shuffle_game(game.student_id, game.id, 0, db)
    new_ids = [row.location_id for row in _live(game, db)[2:]]
    quit_game(game.student_id, game.id, db)
    # a random draw would try the shuffled-away locations first
    monkeypatch.setattr(
        "app.game.draw.random.shuffle", lambda ids: ids.sort(key=lambda i: i not in eligible[2:5])
    )

    drawn = draw_locations(game.student_id, None, Language.JA, Difficulty.BASIC, db)

    assert [snapshot.location_id for snapshot in drawn][:3] == new_ids


def test_shuffle_does_not_commit(
    db: Session, game_at_three: Callable[..., GameSession], transaction_calls: list[str]
) -> None:
    game = game_at_three()
    transaction_calls.clear()

    shuffle_game(game.student_id, game.id, 0, db)

    assert "commit" not in transaction_calls
    assert "rollback" not in transaction_calls
