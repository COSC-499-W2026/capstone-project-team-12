import re
from itertools import product
from uuid import uuid4

import pytest
from sqlalchemy.orm import Session

from app.config import get_settings
from app.content.interface import (
    Difficulty,
    Language,
    LocationNotEligibleError,
    eligible_location_ids,
    is_location_active,
    location_snapshot,
)

ALL_MODES = list(product(Language, Difficulty))
ANSWER_FORMAT = re.compile(r"[A-Z]{3}\d{3,4}")
LOCATIONS_PER_LANGUAGE_AND_DIFFICULTY = 30


def test_every_language_and_difficulty_has_the_same_fixed_eligible_locations(db: Session) -> None:
    for language, difficulty in ALL_MODES:
        ids = eligible_location_ids(db, language, difficulty)
        assert len(ids) == LOCATIONS_PER_LANGUAGE_AND_DIFFICULTY
        assert ids == eligible_location_ids(db, Language.JA, Difficulty.BASIC)


def test_eligible_locations_are_active(db: Session) -> None:
    ids = eligible_location_ids(db, Language.FR, Difficulty.ADVANCING)
    assert all(is_location_active(db, location_id) for location_id in ids)


def test_unknown_location_is_not_active(db: Session) -> None:
    assert not is_location_active(db, uuid4())


def test_snapshot_holds_answers_entrances_and_radius(db: Session) -> None:
    location_id = next(iter(eligible_location_ids(db, Language.KO, Difficulty.CHALLENGING)))
    snapshot = location_snapshot(db, location_id, Language.KO, Difficulty.CHALLENGING)
    assert snapshot.location_id == location_id
    assert snapshot.accepted_answers
    assert all(ANSWER_FORMAT.fullmatch(answer) for answer in snapshot.accepted_answers)
    assert snapshot.gps_points
    assert snapshot.radius_meters == get_settings().gps_radius_meters


def test_snapshot_is_repeatable_with_its_own_clue_per_language_and_difficulty(
    db: Session,
) -> None:
    location_id = next(iter(eligible_location_ids(db, Language.JA, Difficulty.BASIC)))
    snapshots = [location_snapshot(db, location_id, *mode) for mode in ALL_MODES]
    assert snapshots == [location_snapshot(db, location_id, *mode) for mode in ALL_MODES]
    assert len({snapshot.clue_id for snapshot in snapshots}) == len(ALL_MODES)
    assert len({snapshot.audio_asset_id for snapshot in snapshots}) == len(ALL_MODES)


def test_snapshot_of_an_ineligible_location_is_refused(db: Session) -> None:
    with pytest.raises(LocationNotEligibleError):
        location_snapshot(db, uuid4(), Language.DE, Difficulty.BASIC)


def test_content_lookups_never_commit_flush_or_roll_back_the_callers_session(
    db: Session, transaction_calls: list[str]
) -> None:
    for location_id in eligible_location_ids(db, Language.ES, Difficulty.ADVANCING):
        is_location_active(db, location_id)
        location_snapshot(db, location_id, Language.ES, Difficulty.ADVANCING)
    assert transaction_calls == []
