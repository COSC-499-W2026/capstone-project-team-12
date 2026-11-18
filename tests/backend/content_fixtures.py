"""Content for game tests: locations a game can draw, and the IDs a session location's snapshot
points at. Registered in conftest.py through `pytest_plugins`.

Game tests take their content from these fixtures instead of asking the content interface or
AudioService for IDs, so they don't depend on how content is stored. A test that starts, resets
or shuffles a game asks `playable_location_ids` for the language and difficulty it plays, even
when it doesn't use the IDs, so that there is content to draw.

The content interface and UploadedAudioService are still stubs, so for now these fixtures hand
out the stubs' IDs (the same 30 fake locations in every language and difficulty). When the real
versions read the content tables (A20), the fixtures will create the rows instead, and the tests
that use them stay as they are.
"""

from collections.abc import Callable
from typing import NamedTuple
from uuid import UUID, uuid4

import pytest
from sqlalchemy import Engine
from sqlalchemy.orm import Session

from app.content import interface as content
from app.models.enums import Difficulty, Language
from app.services.audio import UploadedAudioService

# as many as the content stub serves, and the size of the spec's campus set
PLAYABLE_LOCATIONS = 30


class SnapshotIds(NamedTuple):
    """The content a session location's snapshot points at."""

    location_id: UUID
    clue_id: UUID
    audio_asset_id: UUID


def _playable(language: Language, difficulty: Difficulty, count: int, db: Session) -> list[UUID]:
    ids = sorted(content.eligible_location_ids(language, difficulty, db))
    assert len(ids) >= count, f"needs {count} playable {language}/{difficulty} locations"
    return ids[:count]


@pytest.fixture
def playable_location_ids(db: Session) -> Callable[..., list[UUID]]:
    """make(language=JA, difficulty=BASIC, count=30): the IDs of `count` locations that a game
    in that language and difficulty can draw, sorted. Every call in a test gives the start of
    the same list for the same language and difficulty."""

    def make(
        language: Language = Language.JA,
        difficulty: Difficulty = Difficulty.BASIC,
        count: int = PLAYABLE_LOCATIONS,
    ) -> list[UUID]:
        return _playable(language, difficulty, count, db)

    return make


@pytest.fixture
def limit_playable_locations(
    playable_location_ids: Callable[..., list[UUID]], monkeypatch: pytest.MonkeyPatch
) -> Callable[[int], None]:
    """limit(count): from now on a game in any language and difficulty can draw only the first
    `count` playable ja/basic locations, for a test of too little content. For now this patches
    the content stub; once the fixtures create rows, it will hide the rest instead."""

    def limit(count: int) -> None:
        playable = frozenset(playable_location_ids(count=count))
        monkeypatch.setattr(content, "eligible_location_ids", lambda *_: playable)

    return limit


@pytest.fixture
def ja_basic_content(playable_location_ids: Callable[..., list[UUID]]) -> None:
    """Content for ja/basic games. A file whose games are mostly ja/basic uses it for every
    test (`pytestmark = pytest.mark.usefixtures("ja_basic_content")`); a test in another mode
    asks playable_location_ids for its own."""
    playable_location_ids(Language.JA, Difficulty.BASIC)


@pytest.fixture
def committed_location_ids(test_engine: Engine) -> Callable[..., list[UUID]]:
    """playable_location_ids for a test whose game runs in its own committed sessions, which
    can't see the rows of the `db` fixture. The test deletes the games it committed itself.
    For now this only reads the stub's IDs, so it commits nothing and has nothing to clean up;
    once it creates rows, it must delete them after the test."""

    def make(
        language: Language = Language.JA,
        difficulty: Difficulty = Difficulty.BASIC,
        count: int = PLAYABLE_LOCATIONS,
    ) -> list[UUID]:
        with Session(test_engine) as session:
            return _playable(language, difficulty, count, session)

    return make


@pytest.fixture
def snapshot_ids(db: Session) -> Callable[[], SnapshotIds]:
    """make(): IDs for a session location's snapshot: a location, its clue and the clue's
    primary recording. Don't rely on the location being active or deactivated (the stub's random
    ID counts as deactivated): pass a `location_id` when that matters."""
    audio = UploadedAudioService()

    def make() -> SnapshotIds:
        return SnapshotIds(uuid4(), uuid4(), audio.clue_audio_asset_id(uuid4(), db))

    return make
