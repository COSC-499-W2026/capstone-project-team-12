"""A18 Difficulty levels over HTTP: a game plays the clues of the difficulty the student picked
(Test Case 1), and a difficulty other than 1, 2 or 3 is refused (Test Case 2).

The positive tests have content at the same 30 locations in every difficulty of two languages,
so a game that took a clue of another difficulty or language would show. They play in Korean,
not the fixtures' default Japanese, so falling back to the default would show too. Which
locations a Basic game draws (one building) belongs to #434 and is not tested here.
"""

from collections.abc import Callable
from typing import Any
from uuid import UUID

import pytest
from fastapi.testclient import TestClient
from httpx2 import Response
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.config import get_settings
from app.errors import ErrorCode
from app.models.accounts import User
from app.models.content import AudioAsset, Clue, Location
from app.models.engagement import Class, ClassEnrollment
from app.models.enums import Difficulty, Language, SkipReason, UserRole
from app.models.game import GameSession, SessionLocation
from app.services.storage import get_storage_service

URL = "/api/v1/games"
LANGUAGES = (Language.JA, Language.KO)
# rows a shuffle replaced stay in the game, but are no longer played
LIVE = SessionLocation.skip_reason.is_distinct_from(SkipReason.SHUFFLED)

Send = Callable[..., Response]
AssertPlays = Callable[[Response, Language, Difficulty], UUID]


@pytest.fixture
def content_in_every_mode(playable_location_ids: Callable[..., list[UUID]]) -> None:
    for language in LANGUAGES:
        for difficulty in Difficulty:
            playable_location_ids(language, difficulty)


@pytest.fixture
def send(client: TestClient, auth_headers: Callable[[User], dict[str, str]]) -> Send:
    """A request to URL + path as `user`, with an optional JSON body."""

    def request(user: User, method: str, path: str = "", body: Any = None) -> Response:
        return client.request(method, URL + path, json=body, headers=auth_headers(user))

    return request


@pytest.fixture
def assert_plays(
    db: Session, split_media_url: Callable[[str], tuple[str, int, str]]
) -> AssertPlays:
    """check(response, language, difficulty): the response's game plays only clues of that
    language and difficulty, and its `clue_audio_url` is a signed link to the current
    location's recording. Returns the game's id."""

    def live_clues(game_id: UUID) -> list[tuple[Any, ...]]:
        # per live location: the clue's language and difficulty, whether the clue is the
        # location's own, and whether the snapshot's recording is that clue's primary one
        query = (
            select(
                Clue.language,
                Clue.difficulty,
                Clue.location_id == SessionLocation.location_id,
                AudioAsset.clue_id == Clue.id,
                AudioAsset.is_primary,
            )
            .select_from(SessionLocation)
            .join(Clue, Clue.id == SessionLocation.clue_id)
            .join(AudioAsset, AudioAsset.id == SessionLocation.audio_asset_id)
            .where(SessionLocation.session_id == game_id, LIVE)
        )
        return [tuple(row) for row in db.execute(query)]

    def recording_key(game_id: UUID, position: int) -> str:
        query = (
            select(AudioAsset.file_path)
            .join(SessionLocation, SessionLocation.audio_asset_id == AudioAsset.id)
            .where(SessionLocation.session_id == game_id, SessionLocation.position == position)
        )
        return db.scalars(query.where(LIVE)).one()

    def check(response: Response, language: Language, difficulty: Difficulty) -> UUID:
        assert response.status_code in (200, 201), response.text
        body = response.json()
        game = db.get_one(GameSession, UUID(body["session_id"]))
        assert (game.target_language, game.difficulty) == (language, difficulty)
        clues = live_clues(game.id)
        assert len(clues) == get_settings().locations_per_game
        assert set(clues) == {(language, difficulty, True, True, True)}
        key, expires, signature = split_media_url(body["clue_audio_url"])
        get_storage_service().verify(key, expires, signature)
        assert key == recording_key(game.id, game.current_position)
        return game.id

    return check


def shuffled_rows(game_id: UUID, db: Session) -> int | None:
    shuffled = SessionLocation.skip_reason == SkipReason.SHUFFLED
    return db.scalar(select(func.count()).where(SessionLocation.session_id == game_id, shuffled))


@pytest.mark.usefixtures("content_in_every_mode")
@pytest.mark.parametrize("difficulty", list(Difficulty))
def test_a_game_plays_the_clues_of_the_difficulty_picked(
    send: Send, assert_plays: AssertPlays, make_user: Callable[..., User], difficulty: Difficulty
) -> None:
    student = make_user()

    started = send(student, "POST", body={"difficulty": difficulty, "language": "ko"})

    game_id = assert_plays(started, Language.KO, difficulty)
    status = send(student, "GET", f"/{game_id}")
    assert status.status_code == 200, status.text
    assert status.json()["difficulty"] == difficulty
    assert_plays(send(student, "GET", f"/{game_id}/current-location"), Language.KO, difficulty)


@pytest.mark.usefixtures("content_in_every_mode")
@pytest.mark.parametrize("difficulty", list(Difficulty))
def test_a_shuffle_and_a_reset_keep_the_difficulty(
    send: Send,
    assert_plays: AssertPlays,
    db: Session,
    make_user: Callable[..., User],
    difficulty: Difficulty,
) -> None:
    student = make_user()
    started = send(student, "POST", body={"difficulty": difficulty, "language": "ko"})
    game_id = assert_plays(started, Language.KO, difficulty)

    shuffled = send(student, "POST", f"/{game_id}/shuffle", {"shuffles_used": 0})

    assert shuffled.status_code == 200, shuffled.text
    assert (shuffled.json()["shuffles_used"], shuffled.json()["difficulty"]) == (1, difficulty)
    # every location was unfinished, so all of them were drawn again
    assert shuffled_rows(game_id, db) == get_settings().locations_per_game
    assert assert_plays(shuffled, Language.KO, difficulty) == game_id

    reset = send(student, "POST", f"/{game_id}/reset")

    assert assert_plays(reset, Language.KO, difficulty) != game_id


@pytest.mark.usefixtures("content_in_every_mode")
def test_a_class_game_plays_the_class_language_at_the_difficulty_picked(
    send: Send,
    assert_plays: AssertPlays,
    make_user: Callable[..., User],
    make_class: Callable[..., Class],
    make_enrollment: Callable[..., ClassEnrollment],
) -> None:
    student, school_class = make_user(), make_class(target_language=Language.KO)
    make_enrollment(student, school_class)
    body = {"difficulty": Difficulty.CHALLENGING, "class_id": str(school_class.id)}

    started = send(student, "POST", body=body)

    assert_plays(started, Language.KO, Difficulty.CHALLENGING)


@pytest.mark.parametrize("difficulty", [4, 0, -1, "basic", None])
def test_a_difficulty_other_than_1_2_or_3_is_422_and_starts_no_game(
    send: Send,
    db: Session,
    make_user: Callable[..., User],
    assert_error: Callable[..., None],
    difficulty: Any,
) -> None:
    student = make_user()

    response = send(student, "POST", body={"difficulty": difficulty, "language": "ja"})

    assert_error(response, 422, ErrorCode.VALIDATION_ERROR)
    details = response.json()["error"]["details"]
    assert [(detail["in"], detail["field"]) for detail in details] == [("body", "difficulty")]
    games = select(func.count()).where(GameSession.student_id == student.id)
    assert db.scalar(games) == 0


def test_a_clue_at_difficulty_4_is_422_and_saves_no_clue(
    client: TestClient,
    auth_headers: Callable[[User], dict[str, str]],
    db: Session,
    make_user: Callable[..., User],
    make_location: Callable[..., Location],
    assert_error: Callable[..., None],
) -> None:
    manager = make_user(role=UserRole.STAFF, can_manage_content=True)
    location = make_location()
    path = f"/api/v1/content/locations/{location.id}/clues/4/ja"

    response = client.put(path, json={"text": "yon"}, headers=auth_headers(manager))

    assert_error(response, 422, ErrorCode.VALIDATION_ERROR)
    clues = select(func.count()).where(Clue.location_id == location.id)
    assert db.scalar(clues) == 0
