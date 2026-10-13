import math
import re
from itertools import combinations, product
from uuid import UUID, uuid4

import pytest
from sqlalchemy.orm import Session

from app.config import get_settings
from app.content.interface import (
    Difficulty,
    GpsPoint,
    Language,
    LocationNotEligibleError,
    eligible_location_ids,
    is_location_active,
    location_snapshot,
)
from app.services.audio import MediaNotFoundError, UploadedAudioService, get_audio_service

ALL_MODES = list(product(Language, Difficulty))
ANSWER_FORMAT = re.compile(r"[A-Z]{3}\d{3,4}")
LOCATIONS_PER_LANGUAGE_AND_DIFFICULTY = 30
METERS_PER_DEGREE_OF_LATITUDE = 111_320


def distance_meters(a: GpsPoint, b: GpsPoint) -> float:
    """Straight-line distance; accurate to well under a metre at campus scale."""
    meters_per_degree_of_longitude = METERS_PER_DEGREE_OF_LATITUDE * math.cos(math.radians(a.lat))
    return math.hypot(
        (a.lat - b.lat) * METERS_PER_DEGREE_OF_LATITUDE,
        (a.lng - b.lng) * meters_per_degree_of_longitude,
    )


class WithoutRecordings(UploadedAudioService):
    def clue_audio_asset_id(self, clue_id: UUID, db: Session) -> UUID:
        raise MediaNotFoundError


class MissingAPhraseIn(UploadedAudioService):
    """Every feedback phrase exists, except that `language` lacks at least one."""

    def __init__(self, language: Language) -> None:
        self.language = language

    def has_all_feedback_phrases(self, language: Language, db: Session) -> bool:
        return language != self.language


def use_audio_service(monkeypatch: pytest.MonkeyPatch, service: UploadedAudioService) -> None:
    monkeypatch.setattr("app.content.interface.get_audio_service", lambda: service)


@pytest.mark.parametrize(("language", "difficulty"), ALL_MODES)
def test_eligible_locations_are_repeatable_for_each_language_and_difficulty(
    db: Session, language: Language, difficulty: Difficulty
) -> None:
    ids = eligible_location_ids(language, difficulty, db)
    assert len(ids) == LOCATIONS_PER_LANGUAGE_AND_DIFFICULTY
    assert ids == eligible_location_ids(language, difficulty, db)


def test_eligible_locations_are_active(db: Session) -> None:
    ids = eligible_location_ids(Language.FR, Difficulty.ADVANCING, db)
    assert all(is_location_active(location_id, db) for location_id in ids)


def test_unknown_location_is_not_active(db: Session) -> None:
    assert not is_location_active(uuid4(), db)


def test_snapshot_holds_answers_entrances_and_radius(db: Session) -> None:
    location_id = next(iter(eligible_location_ids(Language.KO, Difficulty.CHALLENGING, db)))
    snapshot = location_snapshot(location_id, Language.KO, Difficulty.CHALLENGING, db)
    assert snapshot.location_id == location_id
    assert snapshot.accepted_answers
    assert all(ANSWER_FORMAT.fullmatch(answer) for answer in snapshot.accepted_answers)
    assert snapshot.gps_points
    assert snapshot.radius_meters == get_settings().gps_radius_meters


def test_snapshot_repr_hides_answers_and_entrances(db: Session) -> None:
    location_id = next(iter(eligible_location_ids(Language.ZH, Difficulty.BASIC, db)))
    snapshot = location_snapshot(location_id, Language.ZH, Difficulty.BASIC, db)
    coordinates = [str(value) for point in snapshot.gps_points for value in (point.lat, point.lng)]
    hidden = [*snapshot.accepted_answers, *coordinates]
    assert not any(value in repr(snapshot) for value in hidden)
    assert str(snapshot.location_id) in repr(snapshot)


def test_snapshot_is_repeatable_with_its_own_clue_per_language_and_difficulty(
    db: Session,
) -> None:
    location_id = next(iter(eligible_location_ids(Language.JA, Difficulty.BASIC, db)))
    snapshots = [location_snapshot(location_id, *mode, db) for mode in ALL_MODES]
    assert snapshots == [location_snapshot(location_id, *mode, db) for mode in ALL_MODES]
    assert len({snapshot.clue_id for snapshot in snapshots}) == len(ALL_MODES)
    assert len({snapshot.audio_asset_id for snapshot in snapshots}) == len(ALL_MODES)


def test_snapshot_recording_is_one_audio_service_can_play(db: Session) -> None:
    location_id = next(iter(eligible_location_ids(Language.FR, Difficulty.BASIC, db)))
    snapshot = location_snapshot(location_id, Language.FR, Difficulty.BASIC, db)
    audio = get_audio_service()
    assert audio.clue_audio_url(snapshot.audio_asset_id, db)
    assert snapshot.audio_asset_id == audio.clue_audio_asset_id(snapshot.clue_id, db)


def test_stub_arrival_circles_never_overlap(db: Session) -> None:
    ids = eligible_location_ids(Language.JA, Difficulty.BASIC, db)
    snapshots = [location_snapshot(i, Language.JA, Difficulty.BASIC, db) for i in ids]
    entrances = [(point, s.radius_meters) for s in snapshots for point in s.gps_points]
    for (a, radius_a), (b, radius_b) in combinations(entrances, 2):
        assert distance_meters(a, b) > radius_a + radius_b


def test_snapshot_of_an_ineligible_location_is_refused(db: Session) -> None:
    with pytest.raises(LocationNotEligibleError):
        location_snapshot(uuid4(), Language.DE, Difficulty.BASIC, db)


def test_snapshot_of_a_location_without_a_recording_is_refused(
    db: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    use_audio_service(monkeypatch, WithoutRecordings())
    location_id = next(iter(eligible_location_ids(Language.DE, Difficulty.BASIC, db)))
    with pytest.raises(LocationNotEligibleError):
        location_snapshot(location_id, Language.DE, Difficulty.BASIC, db)


@pytest.mark.parametrize("difficulty", Difficulty)
def test_a_language_missing_a_feedback_phrase_has_no_eligible_locations(
    db: Session, monkeypatch: pytest.MonkeyPatch, difficulty: Difficulty
) -> None:
    use_audio_service(monkeypatch, MissingAPhraseIn(Language.KO))
    assert eligible_location_ids(Language.KO, difficulty, db) == frozenset()
    assert len(eligible_location_ids(Language.JA, difficulty, db)) == (
        LOCATIONS_PER_LANGUAGE_AND_DIFFICULTY
    )


def test_snapshot_in_a_language_missing_a_feedback_phrase_is_refused(
    db: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A phrase removed after game fetched the eligible locations still stops the draw."""
    location_id = next(iter(eligible_location_ids(Language.KO, Difficulty.BASIC, db)))
    use_audio_service(monkeypatch, MissingAPhraseIn(Language.KO))
    with pytest.raises(LocationNotEligibleError):
        location_snapshot(location_id, Language.KO, Difficulty.BASIC, db)
    assert location_snapshot(location_id, Language.JA, Difficulty.BASIC, db)


def test_content_lookups_never_commit_flush_or_roll_back_the_callers_session(
    db: Session, transaction_calls: list[str]
) -> None:
    for location_id in eligible_location_ids(Language.ES, Difficulty.ADVANCING, db):
        is_location_active(location_id, db)
        location_snapshot(location_id, Language.ES, Difficulty.ADVANCING, db)
    assert transaction_calls == []
    # With autoflush off nothing is flushed, but the caller's next commit would still save
    # anything a lookup added, changed or deleted.
    assert not (db.new or db.dirty or db.deleted)
