import io
from collections import Counter
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Any
from uuid import UUID

import pytest
from sqlalchemy import delete, event, select, text
from sqlalchemy.orm import Session

from app.content.clue_audio import add_clue_recording
from app.content.coverage import coverage_report
from app.content.schemas import CombinationCoverage, CoverageReport, LocationCoverage
from app.models.accounts import User
from app.models.content import AudioAsset, Clue, FeedbackAudio, Location
from app.models.enums import Difficulty, FeedbackCode, Language

FR_2 = (Language.FR, Difficulty.ADVANCING)
EVERY_COMBINATION = [(language, difficulty) for language in Language for difficulty in Difficulty]


@pytest.fixture
def statements(db: Session) -> Iterator[list[str]]:
    """The SQL sent on the test's connection while the test runs, in order."""
    sent: list[str] = []
    connection = db.connection()

    def record(_conn: Any, _cursor: Any, statement: str, *_rest: Any) -> None:
        sent.append(statement)

    event.listen(connection, "before_cursor_execute", record)
    yield sent
    event.remove(connection, "before_cursor_execute", record)


@pytest.fixture
def record_phrases(
    db: Session, make_feedback_audio: Callable[..., FeedbackAudio]
) -> Callable[..., None]:
    """Make `language` have every feedback phrase except those in `missing`, whatever rows the
    database already holds (the test's transaction is rolled back)."""

    def record(language: Language, missing: tuple[FeedbackCode, ...] = ()) -> None:
        in_language = FeedbackAudio.language == language
        db.execute(delete(FeedbackAudio).where(in_language, FeedbackAudio.code.in_(missing)))
        present = set(db.scalars(select(FeedbackAudio.code).where(in_language)))
        for code in FeedbackCode:
            if code not in present and code not in missing:
                make_feedback_audio(code, language)

    return record


@pytest.fixture
def primary_recording(
    make_clue: Callable[..., Clue], make_audio_asset: Callable[..., AudioAsset]
) -> Callable[..., AudioAsset]:
    """A primary recording of the location's clue in `language` and `difficulty`."""

    def make(location: Location, language: Language, difficulty: Difficulty) -> AudioAsset:
        clue = make_clue(location, language=language, difficulty=difficulty)
        return make_audio_asset(clue, is_primary=True)

    return make


def location_in(report: CoverageReport, location: Location) -> LocationCoverage:
    return next(item for item in report.locations if item.id == location.id)


def combination(
    report: CoverageReport, location: Location, language: Language, difficulty: Difficulty
) -> CombinationCoverage:
    combinations = location_in(report, location).combinations
    return next(c for c in combinations if (c.language, c.difficulty) == (language, difficulty))


def playable(report: CoverageReport, location: Location) -> list[tuple[Language, Difficulty]]:
    combinations = location_in(report, location).combinations
    return [(c.language, c.difficulty) for c in combinations if c.playable]


def playable_counts(report: CoverageReport) -> Counter[tuple[Language, Difficulty]]:
    return Counter(
        {
            (item.language, count.difficulty): count.playable_locations
            for item in report.languages
            for count in item.difficulties
        }
    )


def test_uploaded_mp3_makes_the_location_playable_in_that_language_and_difficulty(
    db: Session,
    stored_media_dir: Path,
    make_location: Callable[..., Location],
    make_user: Callable[..., User],
    make_audio: Callable[..., bytes],
    record_phrases: Callable[..., None],
) -> None:
    location = make_location()
    record_phrases(Language.FR)
    staff = make_user(role="staff", can_manage_content=True)

    add_clue_recording(
        location.id, Difficulty.ADVANCING, Language.FR, io.BytesIO(make_audio(3)), staff.id, db
    )

    report = coverage_report(db)
    assert playable(report, location) == [FR_2]
    assert combination(report, location, *FR_2).has_primary_audio


def test_location_without_clues_lists_every_combination_as_not_playable(
    db: Session, make_location: Callable[..., Location]
) -> None:
    location = make_location(is_active=False)

    item = location_in(coverage_report(db), location)

    assert (item.name, item.room_number, item.is_active) == (
        location.name,
        location.room_number,
        False,
    )
    assert [(c.language, c.difficulty) for c in item.combinations] == EVERY_COMBINATION
    assert not any(c.has_clue_text or c.has_primary_audio or c.playable for c in item.combinations)


def test_locations_are_listed_by_room_number(
    db: Session,
    make_location: Callable[..., Location],
    primary_recording: Callable[..., AudioAsset],
) -> None:
    # Created last to first, and named and given IDs so that sorting by name or by id, either
    # way, gives a different order than the room number does.
    last = make_location(
        id=UUID(int=1), name="A", room_number="ZZZ 300", accepted_answers=["ZZZ300"]
    )
    middle = make_location(
        id=UUID(int=3), name="C", room_number="MMM 200", accepted_answers=["MMM200"]
    )
    first = make_location(
        id=UUID(int=2), name="B", room_number="AAA 100", accepted_answers=["AAA100"]
    )
    primary_recording(middle, *FR_2)  # several rows for one location stay together
    primary_recording(middle, Language.JA, Difficulty.BASIC)

    ids = [item.id for item in coverage_report(db).locations]

    ours = {first.id, middle.id, last.id}
    assert [i for i in ids if i in ours] == [first.id, middle.id, last.id]
    assert ids.count(middle.id) == 1


def test_location_without_a_room_number_is_not_playable(
    db: Session,
    make_location: Callable[..., Location],
    primary_recording: Callable[..., AudioAsset],
    record_phrases: Callable[..., None],
) -> None:
    # The CHECK makes this impossible today, but the spec's definition names the room number and
    # other verification types are Future Work. Dropped inside the test's transaction.
    db.execute(text("ALTER TABLE locations DROP CONSTRAINT ck_locations_room_number_required"))
    record_phrases(Language.FR)
    without = make_location(room_number=None, accepted_answers=["TST999"])
    with_room = make_location()
    primary_recording(without, *FR_2)
    primary_recording(with_room, *FR_2)

    report = coverage_report(db)

    assert combination(report, without, *FR_2).has_primary_audio
    assert playable(report, without) == []
    assert playable(report, with_room) == [FR_2]


def test_missing_feedback_phrase_makes_nothing_in_the_language_playable(
    db: Session,
    make_location: Callable[..., Location],
    primary_recording: Callable[..., AudioAsset],
    record_phrases: Callable[..., None],
) -> None:
    location = make_location()
    primary_recording(location, *FR_2)
    record_phrases(Language.FR, missing=(FeedbackCode.SESSION_COMPLETE, FeedbackCode.NAV_START))

    report = coverage_report(db)

    assert playable(report, location) == []
    missing = {item.language: item.missing_feedback_codes for item in report.languages}
    # in FeedbackCode order
    assert missing[Language.FR] == [FeedbackCode.NAV_START, FeedbackCode.SESSION_COMPLETE]


def test_inactive_location_is_not_playable(
    db: Session,
    make_location: Callable[..., Location],
    primary_recording: Callable[..., AudioAsset],
    record_phrases: Callable[..., None],
) -> None:
    location = make_location(is_active=False)
    primary_recording(location, *FR_2)
    record_phrases(Language.FR)

    report = coverage_report(db)

    assert combination(report, location, *FR_2).has_primary_audio
    assert playable(report, location) == []


def test_recording_that_is_not_primary_does_not_count(
    db: Session,
    make_location: Callable[..., Location],
    make_clue: Callable[..., Clue],
    make_audio_asset: Callable[..., AudioAsset],
    record_phrases: Callable[..., None],
) -> None:
    location = make_location()
    make_audio_asset(make_clue(location, language=Language.FR, difficulty=Difficulty.ADVANCING))
    record_phrases(Language.FR)

    report = coverage_report(db)

    assert not combination(report, location, *FR_2).has_primary_audio
    assert playable(report, location) == []


def test_clue_text_is_reported_but_does_not_make_the_location_playable(
    db: Session,
    make_location: Callable[..., Location],
    make_clue: Callable[..., Clue],
    record_phrases: Callable[..., None],
) -> None:
    location = make_location()
    make_clue(location, language=Language.FR, difficulty=Difficulty.ADVANCING, text="Salle 333")
    make_clue(location, language=Language.FR, difficulty=Difficulty.BASIC)  # no transcript
    record_phrases(Language.FR)

    report = coverage_report(db)

    assert combination(report, location, *FR_2).has_clue_text
    assert not combination(report, location, Language.FR, Difficulty.BASIC).has_clue_text
    assert playable(report, location) == []


def test_languages_count_playable_locations_per_difficulty(
    db: Session,
    make_location: Callable[..., Location],
    primary_recording: Callable[..., AudioAsset],
    record_phrases: Callable[..., None],
) -> None:
    record_phrases(Language.JA)
    record_phrases(Language.FR, missing=(FeedbackCode.NAV_START,))
    before = playable_counts(coverage_report(db))  # locations the database already holds
    first, second = make_location(), make_location()
    primary_recording(first, Language.JA, Difficulty.BASIC)
    primary_recording(second, Language.JA, Difficulty.BASIC)
    primary_recording(second, Language.JA, Difficulty.ADVANCING)
    primary_recording(make_location(is_active=False), Language.JA, Difficulty.CHALLENGING)
    primary_recording(first, *FR_2)

    report = coverage_report(db)

    assert [item.language for item in report.languages] == list(Language)
    assert [item.difficulty for item in report.languages[0].difficulties] == list(Difficulty)
    # nothing in French: a phrase is missing
    assert playable_counts(report) - before == Counter(
        {(Language.JA, Difficulty.BASIC): 2, (Language.JA, Difficulty.ADVANCING): 1}
    )


def test_report_takes_two_queries_however_many_locations_there_are(
    db: Session,
    make_location: Callable[..., Location],
    primary_recording: Callable[..., AudioAsset],
    make_clue: Callable[..., Clue],
    statements: list[str],
) -> None:
    for language, difficulty in EVERY_COMBINATION[::4]:
        primary_recording(make_location(), language, difficulty)
        make_clue(make_location(), language=language, difficulty=difficulty, text="Clue")
    statements.clear()  # the factories flushed

    coverage_report(db)

    assert len(statements) == 2


def test_report_only_reads(
    db: Session, make_location: Callable[..., Location], transaction_calls: list[str]
) -> None:
    make_location()
    transaction_calls.clear()  # creating the location flushed

    coverage_report(db)

    assert transaction_calls == []
