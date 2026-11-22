import math
from collections.abc import Callable
from itertools import combinations, product
from typing import Any
from uuid import UUID, uuid4

import pytest
from sqlalchemy import delete
from sqlalchemy.orm import Session

from app.config import get_settings
from app.content.coverage import coverage_report
from app.content.interface import (
    Difficulty,
    GpsPoint,
    Language,
    LocationNotEligibleError,
    LocationSnapshot,
    eligible_location_ids,
    is_location_active,
    location_snapshot,
)
from app.models.content import AudioAsset, Clue, FeedbackAudio, Location
from app.models.enums import FeedbackCode
from app.models.game import GameSession
from app.services.audio import MediaNotFoundError, UploadedAudioService, get_audio_service

ALL_MODES = list(product(Language, Difficulty))
FR_2 = (Language.FR, Difficulty.ADVANCING)
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


def use_audio_service(monkeypatch: pytest.MonkeyPatch, service: UploadedAudioService) -> None:
    monkeypatch.setattr("app.content.interface.get_audio_service", lambda: service)


@pytest.fixture(autouse=True)
def every_feedback_phrase(feedback_phrases: Callable[[Language], None]) -> None:
    """Every phrase in every language, so only the locations decide what is eligible."""
    for language in Language:
        feedback_phrases(language)


@pytest.fixture
def drawable_clue(
    make_location: Callable[..., Location],
    make_clue: Callable[..., Clue],
    make_audio_asset: Callable[..., AudioAsset],
) -> Callable[..., Clue]:
    """make(language=FR, difficulty=ADVANCING, primary=True, **location_fields): the clue of a new
    location in that language and difficulty, with one recording (primary unless told)."""

    def make(
        language: Language = FR_2[0],
        difficulty: Difficulty = FR_2[1],
        primary: bool = True,
        **fields: Any,
    ) -> Clue:
        clue = make_clue(make_location(**fields), language=language, difficulty=difficulty)
        make_audio_asset(clue, is_primary=primary)
        return clue

    return make


# Each makes a location that fr/advancing games must not draw. A location without a room number
# cannot be stored (Stage 2 reads the room number), so it has no case here.
INELIGIBLE: dict[str, Callable[[Callable[..., Clue], Callable[..., Location]], UUID]] = {
    "inactive": lambda clue, _: clue(is_active=False).location_id,
    "clue in another language": lambda clue, _: clue(language=Language.JA).location_id,
    "clue at another difficulty": lambda clue, _: clue(difficulty=Difficulty.BASIC).location_id,
    "recording not primary": lambda clue, _: clue(primary=False).location_id,
    "no clue": lambda _, location: location().id,
}


def test_a_drawable_location_is_eligible(db: Session, drawable_clue: Callable[..., Clue]) -> None:
    clue = drawable_clue()
    assert clue.location_id in eligible_location_ids(*FR_2, db)


@pytest.mark.parametrize("case", INELIGIBLE)
def test_an_ineligible_location_is_neither_eligible_nor_snapshotted(
    db: Session,
    drawable_clue: Callable[..., Clue],
    make_location: Callable[..., Location],
    case: str,
) -> None:
    location_id = INELIGIBLE[case](drawable_clue, make_location)
    assert location_id not in eligible_location_ids(*FR_2, db)
    with pytest.raises(LocationNotEligibleError):
        location_snapshot(location_id, *FR_2, db)


def test_a_language_missing_a_feedback_phrase_has_no_eligible_locations(
    db: Session, drawable_clue: Callable[..., Clue]
) -> None:
    # one difficulty: the phrase check comes before difficulty matters
    difficulty = Difficulty.ADVANCING
    korean = drawable_clue(Language.KO, difficulty).location_id
    japanese = drawable_clue(Language.JA, difficulty).location_id
    assert korean in eligible_location_ids(Language.KO, difficulty, db)
    db.execute(
        delete(FeedbackAudio).where(
            FeedbackAudio.code == FeedbackCode.NAV_GPS_WEAK, FeedbackAudio.language == Language.KO
        )
    )
    assert eligible_location_ids(Language.KO, difficulty, db) == frozenset()
    assert japanese in eligible_location_ids(Language.JA, difficulty, db)


def test_eligible_locations_agree_with_the_coverage_report(
    db: Session, drawable_clue: Callable[..., Clue], make_location: Callable[..., Location]
) -> None:
    for make_ineligible in INELIGIBLE.values():
        make_ineligible(drawable_clue, make_location)
    for language in (Language.FR, Language.JA, Language.KO):
        drawable_clue(language, Difficulty.ADVANCING)
    # without its phrases, Korean's drawable location is not playable
    db.execute(delete(FeedbackAudio).where(FeedbackAudio.language == Language.KO))
    report = coverage_report(db)
    for language, difficulty in ALL_MODES:
        playable = {
            location.id
            for location in report.locations
            for combination in location.combinations
            if (combination.language, combination.difficulty, combination.playable)
            == (language, difficulty, True)
        }
        assert eligible_location_ids(language, difficulty, db) == playable


def test_only_an_active_location_is_active(
    db: Session, make_location: Callable[..., Location]
) -> None:
    assert is_location_active(make_location().id, db)
    assert not is_location_active(make_location(is_active=False).id, db)
    assert not is_location_active(uuid4(), db)


def test_snapshot_holds_the_locations_answers_entrances_radius_clue_and_primary_recording(
    db: Session, drawable_clue: Callable[..., Clue], make_audio_asset: Callable[..., AudioAsset]
) -> None:
    radius = get_settings().gps_radius_meters + 15  # not the setting: the location's own
    entrances = [{"lat": 49.9401, "lng": -119.3952}, {"lat": 49.9398, "lng": -119.3961}]
    clue = drawable_clue(
        primary=False,
        accepted_answers=["SCI333", "SCI3330"],
        gps_points=entrances,
        gps_radius_meters=radius,
    )
    primary = make_audio_asset(clue, is_primary=True)

    snapshot = location_snapshot(clue.location_id, *FR_2, db)

    assert snapshot == LocationSnapshot(
        location_id=clue.location_id,
        clue_id=clue.id,
        audio_asset_id=primary.id,
        accepted_answers=("SCI333", "SCI3330"),
        gps_points=(GpsPoint(49.9401, -119.3952), GpsPoint(49.9398, -119.3961)),
        radius_meters=radius,
    )


def test_snapshot_repr_hides_answers_and_entrances(
    db: Session, drawable_clue: Callable[..., Clue]
) -> None:
    location_id = drawable_clue().location_id
    snapshot = location_snapshot(location_id, *FR_2, db)
    coordinates = [str(value) for point in snapshot.gps_points for value in (point.lat, point.lng)]
    hidden = [*snapshot.accepted_answers, *coordinates]
    assert not any(value in repr(snapshot) for value in hidden)
    assert str(snapshot.location_id) in repr(snapshot)


def test_snapshot_of_an_unknown_location_is_refused(db: Session) -> None:
    with pytest.raises(LocationNotEligibleError):
        location_snapshot(uuid4(), *FR_2, db)


def test_snapshot_of_a_location_without_a_recording_is_refused(
    db: Session, drawable_clue: Callable[..., Clue], monkeypatch: pytest.MonkeyPatch
) -> None:
    location_id = drawable_clue().location_id
    use_audio_service(monkeypatch, WithoutRecordings())
    with pytest.raises(LocationNotEligibleError):
        location_snapshot(location_id, *FR_2, db)


def test_playable_test_locations_have_no_overlapping_arrival_circles(
    db: Session, playable_location_ids: Callable[..., list[UUID]]
) -> None:
    """Game tests rely on this: a point is never inside two locations' circles."""
    ids = playable_location_ids(Language.JA, Difficulty.BASIC)
    snapshots = [location_snapshot(i, Language.JA, Difficulty.BASIC, db) for i in ids]
    entrances = [(point, s.radius_meters) for s in snapshots for point in s.gps_points]
    for (a, radius_a), (b, radius_b) in combinations(entrances, 2):
        assert distance_meters(a, b) > radius_a + radius_b


def test_session_rows_point_at_real_content_and_never_widen_a_draw(
    db: Session,
    make_game_session: Callable[..., GameSession],
    playable_location_ids: Callable[..., list[UUID]],
    snapshot_ids: Callable[..., tuple[UUID, UUID, UUID]],
) -> None:
    """Game tests rely on this: a row at a playable location has the IDs a draw would give it,
    and a row with its own location adds nothing a game could draw."""
    game = make_game_session()  # ja/basic
    [playable] = playable_location_ids(Language.JA, Difficulty.BASIC, count=1)
    drawn = location_snapshot(playable, Language.JA, Difficulty.BASIC, db)
    assert snapshot_ids(game, playable) == (drawn.location_id, drawn.clue_id, drawn.audio_asset_id)

    location_id, _, audio_asset_id = snapshot_ids(game)
    assert location_id not in eligible_location_ids(Language.JA, Difficulty.BASIC, db)
    get_audio_service().clue_audio_url(audio_asset_id, db)  # a recording that exists


def test_content_lookups_never_commit_flush_roll_back_or_load_a_location(
    db: Session, drawable_clue: Callable[..., Clue], transaction_calls: list[str]
) -> None:
    location_id = drawable_clue().location_id
    db.expunge_all()
    transaction_calls.clear()  # the factories flushed what they made

    assert location_id in eligible_location_ids(*FR_2, db)
    assert is_location_active(location_id, db)
    location_snapshot(location_id, *FR_2, db)

    assert transaction_calls == []
    # With autoflush off nothing is flushed, but the caller's next commit would still save
    # anything a lookup added, changed or deleted.
    assert not (db.new or db.dirty or db.deleted)
    # the answers and entrances stay out of the caller's session
    assert not any(isinstance(row, Location) for row in db.identity_map.values())
