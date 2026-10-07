import re
from collections.abc import Callable, Iterator
from itertools import product
from typing import Any
from uuid import uuid4

import pytest
from sqlalchemy import event
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


@pytest.fixture
def transaction_calls(db: Session, monkeypatch: pytest.MonkeyPatch) -> Iterator[list[str]]:
    """Every commit, flush and rollback made on the test session while the test runs.

    Flush is spied on directly: SQLAlchemy only fires its flush events when something is
    pending, so listening for them would miss a flush of a clean session, and the automatic
    flush before a query. The transaction is opened first because rolling back a session
    without one does nothing, which would hide a rollback inside a lookup.
    """
    db.connection()
    calls: list[str] = []
    real_flush = db.flush

    def spy_flush(*args: Any, **kwargs: Any) -> None:
        calls.append("flush")
        real_flush(*args, **kwargs)

    def record(name: str) -> Callable[..., None]:
        return lambda *_: calls.append(name)

    monkeypatch.setattr(db, "flush", spy_flush)
    event.listen(db, "after_commit", record("commit"))
    event.listen(db, "after_soft_rollback", record("rollback"))
    yield calls


@pytest.mark.parametrize(("language", "difficulty"), ALL_MODES)
def test_eligible_locations_are_repeatable_for_each_language_and_difficulty(
    db: Session, language: Language, difficulty: Difficulty
) -> None:
    ids = eligible_location_ids(db, language, difficulty)
    assert len(ids) == LOCATIONS_PER_LANGUAGE_AND_DIFFICULTY
    assert ids == eligible_location_ids(db, language, difficulty)


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


def test_snapshot_repr_hides_answers_and_entrances(db: Session) -> None:
    location_id = next(iter(eligible_location_ids(db, Language.ZH, Difficulty.BASIC)))
    snapshot = location_snapshot(db, location_id, Language.ZH, Difficulty.BASIC)
    hidden = [*snapshot.accepted_answers, *(str(point.lat) for point in snapshot.gps_points)]
    assert not any(value in repr(snapshot) for value in hidden)
    assert str(snapshot.location_id) in repr(snapshot)


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
