"""Content for game tests: locations a game can draw, and the IDs a session location's snapshot
points at. Registered in conftest.py through `pytest_plugins`.

Game tests take their content from these fixtures instead of asking the content interface or
AudioService for IDs, so they don't depend on how content is stored. A test that starts, resets
or shuffles a game asks `playable_location_ids` for the language and difficulty it plays, even
when it doesn't use the IDs, so that there is content to draw. A test that needs a deactivated
location asks `deactivated_location_id` for one instead of making up an id.

The feedback phrases are real rows: a game only starts in a language that has every phrase, and
plays them while it runs, so the fixtures add the ones the database lacks. The content interface
and UploadedAudioService's clue recordings are still stubs, so for now location and recording IDs
are the stubs' (the same 30 fake locations in every language and difficulty). When the real
versions read the content tables (A20), the fixtures will create those rows too, and the tests
that use them stay as they are.
"""

from collections.abc import Callable, Iterator
from typing import NamedTuple
from uuid import UUID, uuid4

import pytest
from sqlalchemy import Engine, delete, select, tuple_
from sqlalchemy.orm import Session

from app.content import interface as content
from app.models.content import FeedbackAudio
from app.models.enums import Difficulty, FeedbackCode, Language
from app.models.game import GameSession
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


def _missing_phrases(language: Language, db: Session) -> list[FeedbackCode]:
    query = select(FeedbackAudio.code).where(FeedbackAudio.language == language)
    recorded = set(db.scalars(query))
    return [code for code in FeedbackCode if code not in recorded]


@pytest.fixture
def feedback_phrases(
    db: Session, make_feedback_audio: Callable[..., FeedbackAudio]
) -> Callable[[Language], None]:
    """make(language): every feedback phrase in `language`, adding only the ones it lacks."""

    def make(language: Language) -> None:
        for code in _missing_phrases(language, db):
            make_feedback_audio(code, language)

    return make


@pytest.fixture
def playable_location_ids(
    db: Session, feedback_phrases: Callable[[Language], None]
) -> Callable[..., list[UUID]]:
    """make(language=JA, difficulty=BASIC, count=30): the IDs of `count` locations that a game
    in that language and difficulty can draw, sorted. Every call in a test gives the start of
    the same list for the same language and difficulty."""

    def make(
        language: Language = Language.JA,
        difficulty: Difficulty = Difficulty.BASIC,
        count: int = PLAYABLE_LOCATIONS,
    ) -> list[UUID]:
        feedback_phrases(language)
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
def committed_location_ids(
    test_engine: Engine, request: pytest.FixtureRequest
) -> Iterator[Callable[..., list[UUID]]]:
    """playable_location_ids for a test whose game runs in its own committed sessions, which
    can't see the rows of the `db` fixture. The test deletes the games it committed itself; this
    commits the feedback phrases the language lacks and deletes them after the test.

    Don't use it in a test that also has `db` content in the same language: the committed
    phrases would wait for the uncommitted ones, so the test fails with a message naming the
    language instead."""
    db = request.getfixturevalue("db") if "db" in request.fixturenames else None
    added: list[tuple[FeedbackCode, Language]] = []

    def make(
        language: Language = Language.JA,
        difficulty: Difficulty = Difficulty.BASIC,
        count: int = PLAYABLE_LOCATIONS,
    ) -> list[UUID]:
        with Session(test_engine) as session:
            missing = _missing_phrases(language, session)
            if db is not None and len(_missing_phrases(language, db)) < len(missing):
                pytest.fail(
                    f"the `db` fixture already holds {language} feedback phrases that only it "
                    "can see, so committing them too would wait on its uncommitted rows. Use "
                    "another language for the committed content, or don't add the phrases to "
                    "`db`."
                )
            session.add_all(
                FeedbackAudio(code=code, language=language, file_path=f"{uuid4()}.mp3")
                for code in missing
            )
            session.commit()
            added.extend((code, language) for code in missing)
            return _playable(language, difficulty, count, session)

    yield make
    if added:
        with Session(test_engine) as session:
            key = tuple_(FeedbackAudio.code, FeedbackAudio.language)
            session.execute(delete(FeedbackAudio).where(key.in_(added)))
            session.commit()


@pytest.fixture
def snapshot_ids(
    db: Session, feedback_phrases: Callable[[Language], None]
) -> Callable[[GameSession], SnapshotIds]:
    """make(game): IDs for the snapshot of a location in `game`: a location, its clue and the
    clue's primary recording. Adds every feedback phrase in the game's language, which a game
    only starts with. Don't rely on the location being active or deactivated (the stub's random
    ID counts as deactivated): pass a `location_id` when that matters."""
    audio = UploadedAudioService()

    def make(game: GameSession) -> SnapshotIds:
        feedback_phrases(Language(game.target_language))
        return SnapshotIds(uuid4(), uuid4(), audio.clue_audio_asset_id(uuid4(), db))

    return make


@pytest.fixture
def deactivated_location_id() -> Callable[[], UUID]:
    """make(): the id of a location that a game must treat as deactivated (skipped or refused).
    For now any id the content stub doesn't serve; once the fixtures create rows, an inactive
    location."""
    return uuid4


@pytest.fixture
def committed_snapshot_ids(test_engine: Engine) -> Callable[[], SnapshotIds]:
    """snapshot_ids for rows committed in their own sessions (see committed_location_ids).
    For now it commits nothing, like committed_location_ids."""
    audio = UploadedAudioService()

    def make() -> SnapshotIds:
        with Session(test_engine) as session:
            return SnapshotIds(uuid4(), uuid4(), audio.clue_audio_asset_id(uuid4(), session))

    return make


@pytest.fixture
def committed_deactivated_location_id() -> Callable[[], UUID]:
    """deactivated_location_id for rows committed in their own sessions. For now it commits
    nothing, like committed_location_ids."""
    return uuid4
