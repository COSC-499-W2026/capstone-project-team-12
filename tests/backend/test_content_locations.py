from collections.abc import Callable
from typing import Any
from uuid import uuid4

import pytest
from pydantic import ValidationError
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.config import get_settings
from app.content.locations import (
    create_location,
    deactivate_location,
    list_locations,
    replace_location,
    set_clue_text,
)
from app.content.schemas import INT32_MAX, LocationFields, LocationOut
from app.errors import AppError, ErrorCode
from app.models.content import Clue, Location
from app.models.enums import Difficulty, Language

ENTRANCE = {"lat": 49.9394, "lng": -119.3948}


def fields(**overrides: Any) -> LocationFields:
    values: dict[str, Any] = {
        "name": "Science lab",
        "building_name": "Science Building",
        "room_number": "SCI 333",
        "gps_points": [ENTRANCE],
    }
    return LocationFields.model_validate(values | overrides)


@pytest.mark.parametrize("typed", ["SCI 333", "sci 333", "SCI333", "SCI-333", "  Sci 333 "])
def test_room_number_is_stored_as_on_the_sign(typed: str) -> None:
    location = fields(room_number=typed)

    assert location.room_number == "SCI 333"
    assert location.accepted_answers == ["SCI333"]


@pytest.mark.parametrize(
    "typed", ["SC 333", "SCIE 333", "SCI 33", "SCI 33333", "SCI  333", "SCI_333", "SCI ３３３", ""]
)
def test_bad_room_number_is_refused(typed: str) -> None:
    with pytest.raises(ValidationError, match="room_number"):
        fields(room_number=typed)


@pytest.mark.parametrize(
    ("given", "stored"),
    [
        (["sci-333", "SCI333", "sci 334"], ["SCI333", "SCI334"]),
        (["SCI334"], ["SCI333", "SCI334"]),  # the room's own answer is always accepted
        ([], ["SCI333"]),
    ],
)
def test_answers_are_normalised_and_include_the_rooms(given: list[str], stored: list[str]) -> None:
    assert fields(accepted_answers=given).accepted_answers == stored


@pytest.mark.parametrize("answers", [["SCI 33"], ["333"]])
def test_bad_answers_are_refused(answers: list[str]) -> None:
    with pytest.raises(ValidationError, match="accepted_answers"):
        fields(accepted_answers=answers)


@pytest.mark.parametrize(
    "points",
    [
        [],
        [{"lat": 90.1, "lng": 0}],
        [{"lat": -90.1, "lng": 0}],
        [{"lat": 0, "lng": 180.1}],
        [{"lat": 0, "lng": -180.1}],
        [{"lat": 0}],
    ],
)
def test_bad_entrance_points_are_refused(points: list[dict[str, float]]) -> None:
    with pytest.raises(ValidationError, match="gps_points"):
        fields(gps_points=points)


@pytest.mark.parametrize("name_field", ["name", "building_name"])
@pytest.mark.parametrize("bad", ["   ", "Lab\x00"])  # blank, and a NUL the database cannot store
def test_bad_names_are_refused(name_field: str, bad: str) -> None:
    with pytest.raises(ValidationError, match=name_field):
        fields(**{name_field: bad})


@pytest.mark.parametrize(
    ("number_field", "bad"),
    [
        ("gps_radius_meters", 0),
        ("gps_radius_meters", INT32_MAX + 1),
        ("floor_number", INT32_MAX + 1),
        ("floor_number", -INT32_MAX - 1),
    ],
)
def test_numbers_the_database_cannot_hold_are_refused(number_field: str, bad: int) -> None:
    with pytest.raises(ValidationError, match=number_field):
        fields(**{number_field: bad})


def test_create_location_saves_every_field(db: Session) -> None:
    # one building has 4-digit room numbers
    location = create_location(
        fields(name=" Lab ", floor_number=3, room_number="art 1001", gps_points=[ENTRANCE] * 2), db
    )

    db.expire_all()
    saved = db.get(Location, location.id)
    assert saved is not None
    assert LocationOut.model_validate(saved).model_dump(exclude={"id"}) == {
        "name": "Lab",
        "building_name": "Science Building",
        "floor_number": 3,
        "room_number": "ART 1001",
        "accepted_answers": ["ART1001"],
        "gps_points": [ENTRANCE, ENTRANCE],
        "gps_radius_meters": get_settings().gps_radius_meters,
        "is_active": True,
    }


def test_room_number_of_an_inactive_location_cannot_be_reused(
    db: Session, make_location: Callable[..., Location]
) -> None:
    make_location(room_number="SCI 333", accepted_answers=["SCI333"], is_active=False)

    with pytest.raises(AppError) as error:
        create_location(fields(room_number="sci333"), db)

    assert error.value.code == ErrorCode.VALIDATION_ERROR
    details = error.value.details
    assert details == [{"in": "body", "field": "room_number", "message": error.value.message}]


def test_duplicate_room_number_keeps_the_work_done_before_it(
    db: Session, make_location: Callable[..., Location]
) -> None:
    make_location(room_number="SCI 333", accepted_answers=["SCI333"])
    other = make_location()
    kept = create_location(fields(room_number="SCI 444"), db)

    for save in (
        lambda: create_location(fields(), db),
        lambda: replace_location(other.id, fields(), db),
    ):
        with pytest.raises(AppError) as error:
            save()
        assert error.value.code == ErrorCode.VALIDATION_ERROR

    assert db.get(Location, kept.id) is kept
    assert list_locations(db)  # the session is still usable


def test_other_constraint_errors_are_not_reported_as_a_duplicate(db: Session) -> None:
    unchecked = fields().model_copy(update={"gps_radius_meters": 0})  # skips validation

    with pytest.raises(IntegrityError, match="ck_locations_gps_radius_meters"):
        create_location(unchecked, db)


def test_list_locations_includes_inactive_ones_by_room_number(
    db: Session, make_location: Callable[..., Location]
) -> None:
    later = make_location(room_number="ZZZ 999", accepted_answers=["ZZZ999"])
    earlier = make_location(room_number="AAA 100", accepted_answers=["AAA100"], is_active=False)

    listed = list_locations(db)

    assert listed.index(earlier) < listed.index(later)


def test_replace_location_replaces_every_field_and_can_reactivate(
    db: Session, make_location: Callable[..., Location]
) -> None:
    # same room number as the replacement: keeping one's own room number is allowed
    location = make_location(
        room_number="SCI 333", accepted_answers=["SCI333"], is_active=False, floor_number=2
    )

    replaced = replace_location(location.id, fields(accepted_answers=["SCI333", "SCI334"]), db)

    db.expire_all()
    assert replaced.id == location.id
    assert replaced.room_number == "SCI 333"
    assert replaced.accepted_answers == ["SCI333", "SCI334"]
    assert replaced.floor_number is None
    assert replaced.is_active


def test_deactivate_location_keeps_the_row(
    db: Session, make_location: Callable[..., Location]
) -> None:
    location = make_location()

    deactivate_location(location.id, db)
    deactivate_location(location.id, db)

    db.expire_all()
    assert db.get(Location, location.id) is not None
    assert not location.is_active


@pytest.mark.parametrize(
    "call",
    [
        lambda db: replace_location(uuid4(), fields(), db),
        lambda db: deactivate_location(uuid4(), db),
        lambda db: set_clue_text(uuid4(), Difficulty.BASIC, Language.JA, "text", db),
    ],
)
def test_unknown_location_is_not_found(db: Session, call: Callable[[Session], object]) -> None:
    with pytest.raises(AppError) as error:
        call(db)

    assert error.value.code == ErrorCode.NOT_FOUND


def test_set_clue_text_creates_the_clue_then_updates_it(
    db: Session, make_location: Callable[..., Location]
) -> None:
    location = make_location()

    created = set_clue_text(location.id, Difficulty.ADVANCING, Language.FR, " Salle 333 ", db)
    updated = set_clue_text(location.id, Difficulty.ADVANCING, Language.FR, "Salle trois", db)

    db.expire_all()
    assert updated.id == created.id
    assert (updated.difficulty, updated.language, updated.text) == (2, "fr", "Salle trois")


def test_clue_text_the_database_cannot_hold_is_refused(
    db: Session, make_location: Callable[..., Location]
) -> None:
    with pytest.raises(AppError) as error:
        set_clue_text(make_location().id, Difficulty.BASIC, Language.JA, "a\x00b", db)

    assert error.value.code == ErrorCode.VALIDATION_ERROR
    assert error.value.details == [{"in": "body", "field": "text", "message": error.value.message}]


@pytest.mark.parametrize("text", ["", "   ", None])
def test_blank_clue_text_is_stored_as_null(
    db: Session, make_clue: Callable[..., Clue], text: str | None
) -> None:
    clue = make_clue(text="old")

    set_clue_text(clue.location_id, Difficulty.BASIC, Language.JA, text, db)

    db.refresh(clue)
    assert clue.text is None


def test_services_flush_but_never_commit(
    db: Session, monkeypatch: pytest.MonkeyPatch, transaction_calls: list[str]
) -> None:
    # The savepoints of create and replace fire commit events too, so spy on Session.commit itself
    committed: list[str] = []
    monkeypatch.setattr(db, "commit", lambda: committed.append("commit"))
    location = create_location(fields(), db)
    replace_location(location.id, fields(name="Renamed"), db)
    set_clue_text(location.id, Difficulty.BASIC, Language.JA, "text", db)
    deactivate_location(location.id, db)

    assert "flush" in transaction_calls
    assert not committed
    assert "rollback" not in transaction_calls
