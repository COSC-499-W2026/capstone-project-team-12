"""Content for game tests: locations a game can draw, and the IDs a session location's snapshot
points at. Registered in conftest.py through `pytest_plugins`.

Game tests take their content from these fixtures instead of asking the content interface or
AudioService for IDs, so they don't depend on how content is stored. A test that starts, resets
or shuffles a game asks `playable_location_ids` for the language and difficulty it plays, even
when it doesn't use the IDs, so that there is content to draw. A test that needs a deactivated
location asks `deactivated_location_id` for one instead of making up an id.

The feedback phrases and deactivated locations are real rows: a game only starts in a language
that has every phrase, and plays them while it runs, so the fixtures add the ones the database
lacks. Rows made through the `db` fixture are rolled back with it; the `committed_*` fixtures
commit theirs and delete them after the test. The content interface and UploadedAudioService's
clue recordings are still stubs, so for now playable location and recording IDs are the stubs'
(the same 30 fake locations in every language and difficulty). When the real versions read the
content tables (A20), the fixtures will create those rows too, and the tests that use them stay
as they are.
"""

from collections.abc import Callable, Iterator
from contextlib import AbstractContextManager, contextmanager
from itertools import count
from typing import NamedTuple
from uuid import UUID, uuid4

import pytest
from sqlalchemy import Engine, delete, func, select, text, tuple_
from sqlalchemy.orm import Session

from app.config import get_settings
from app.content import interface as content
from app.models.content import FeedbackAudio, Location
from app.models.enums import Difficulty, FeedbackCode, Language
from app.models.game import GameSession
from app.services.audio import get_audio_service

# as many as the content stub serves, and the size of the spec's campus set
PLAYABLE_LOCATIONS = 30
JA, BASIC = Language.JA, Difficulty.BASIC
CAMPUS_LAT, CAMPUS_LNG = 49.9394, -119.3948  # UBCO
# Building codes of the room numbers made here (make_location uses "TST"). Committed rows have
# their own, so they can't collide with the `db` fixture's rows and the teardown can check that
# none is left behind.
TEST_BUILDING, COMMITTED_BUILDING = "PLY", "RAC"


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


@contextmanager
def _committed(engine: Engine) -> Iterator[Session]:
    """A session of its own, committed at the end. The lock timeout turns a wait for rows the
    `db` fixture holds uncommitted into an error instead of a hang."""
    with Session(engine) as session:
        session.execute(text("SET LOCAL lock_timeout = '10s'"))
        yield session
        session.commit()


class _Content:
    """Adds content rows in the sessions `open_session` gives, which flush or commit them when
    the block ends, and remembers the locations and phrases it added."""

    def __init__(
        self, open_session: Callable[[], AbstractContextManager[Session]], building: str
    ) -> None:
        self._open_session = open_session
        self._building = building
        self._rooms = count(1000)  # 4 digits, which the room number format allows
        self.locations: list[UUID] = []
        self.phrases: list[tuple[FeedbackCode, Language]] = []

    def feedback_phrases(self, language: Language) -> None:
        with self._open_session() as session:
            self._add_phrases(language, session)

    def playable(
        self,
        language: Language = JA,
        difficulty: Difficulty = BASIC,
        count: int = PLAYABLE_LOCATIONS,
    ) -> list[UUID]:
        with self._open_session() as session:
            self._add_phrases(language, session)
            return _playable(language, difficulty, count, session)

    def snapshot(
        self, language: Language, difficulty: Difficulty, location_id: UUID | None
    ) -> SnapshotIds:
        with self._open_session() as session:
            self._add_phrases(language, session)
            clue_id = uuid4()
            audio_asset_id = get_audio_service().clue_audio_asset_id(clue_id, session)
        return SnapshotIds(location_id or uuid4(), clue_id, audio_asset_id)

    def deactivated(self) -> UUID:
        with self._open_session() as session:
            return self._add_location(session, is_active=False)

    def delete(self, engine: Engine) -> None:
        """Deletes what this added, in foreign-key order (the test has deleted its games), and
        checks that no committed location is left."""
        with _committed(engine) as session:
            session.execute(delete(Location).where(Location.id.in_(self.locations)))
            if self.phrases:
                key = tuple_(FeedbackAudio.code, FeedbackAudio.language)
                session.execute(delete(FeedbackAudio).where(key.in_(self.phrases)))
        committed_room = Location.room_number.startswith(f"{COMMITTED_BUILDING} ")
        with Session(engine) as session:
            left = session.scalar(select(func.count()).where(committed_room))
        assert left == 0, f"{left} committed test locations are left in the database"

    def _add_location(self, session: Session, **fields: bool) -> UUID:
        room_number = f"{self._building} {next(self._rooms)}"
        location = Location(
            id=uuid4(),
            name=f"Test room {room_number}",
            building_name="Test Building",
            room_number=room_number,
            accepted_answers=[room_number.replace(" ", "")],
            gps_points=[{"lat": CAMPUS_LAT, "lng": CAMPUS_LNG}],
            gps_radius_meters=get_settings().gps_radius_meters,
            **fields,
        )
        session.add(location)
        self.locations.append(location.id)
        return location.id

    def _add_phrases(self, language: Language, session: Session) -> None:
        missing = _missing_phrases(language, session)
        session.add_all(
            FeedbackAudio(code=code, language=language, file_path=f"{uuid4()}.mp3")
            for code in missing
        )
        session.flush()  # for the queries that follow, even with autoflush off
        self.phrases.extend((code, language) for code in missing)


@pytest.fixture
def db_content(db: Session) -> _Content:
    """Content rows in the test's transaction, rolled back with it."""

    @contextmanager
    def flushed() -> Iterator[Session]:
        yield db
        db.flush()

    return _Content(flushed, TEST_BUILDING)


@pytest.fixture
def feedback_phrases(db_content: _Content) -> Callable[[Language], None]:
    """make(language): every feedback phrase in `language`, adding only the ones it lacks."""
    return db_content.feedback_phrases


@pytest.fixture
def playable_location_ids(db_content: _Content) -> Callable[..., list[UUID]]:
    """make(language=JA, difficulty=BASIC, count=30): the IDs of `count` locations that a game
    in that language and difficulty can draw, sorted. Every call in a test gives the start of
    the same list for the same language and difficulty."""
    return db_content.playable


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
def committed_content(test_engine: Engine) -> Iterator[_Content]:
    """Content rows committed in their own sessions, deleted after the test."""
    rows = _Content(lambda: _committed(test_engine), COMMITTED_BUILDING)
    yield rows
    rows.delete(test_engine)


@pytest.fixture
def committed_location_ids(committed_content: _Content) -> Callable[..., list[UUID]]:
    """playable_location_ids for a test whose game runs in its own committed sessions, which
    can't see the rows of the `db` fixture. The test deletes the games it committed itself; this
    commits the content (feedback phrases included) and deletes it after the test.

    Don't use it in a test that also has `db` content in the same language: the committed
    phrases would wait for the uncommitted ones (lock_timeout turns that into an error)."""
    return committed_content.playable


@pytest.fixture
def snapshot_ids(db_content: _Content) -> Callable[..., SnapshotIds]:
    """make(game, location_id=None): IDs for the snapshot of a location in `game`: the location,
    its clue and the clue's primary recording. Adds every feedback phrase in the game's
    language, which a game only starts with. Without a location_id, don't rely on the location
    being active or deactivated (the stub's random ID counts as deactivated): pass one when that
    matters."""

    def make(game: GameSession, location_id: UUID | None = None) -> SnapshotIds:
        mode = Language(game.target_language), Difficulty(game.difficulty)
        return db_content.snapshot(*mode, location_id)

    return make


@pytest.fixture
def deactivated_location_id(db_content: _Content) -> Callable[[], UUID]:
    """make(): the id of a new location that a game must treat as deactivated (skipped or
    refused)."""
    return db_content.deactivated


@pytest.fixture
def committed_snapshot_ids(committed_content: _Content) -> Callable[[UUID], SnapshotIds]:
    """make(location_id): snapshot_ids for a committed ja/basic game's row at that location."""
    return lambda location_id: committed_content.snapshot(JA, BASIC, location_id)


@pytest.fixture
def committed_deactivated_location_id(committed_content: _Content) -> Callable[[], UUID]:
    """deactivated_location_id for rows committed in their own sessions."""
    return committed_content.deactivated
