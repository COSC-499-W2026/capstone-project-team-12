"""locations and clues reject bad data at the database level."""

import json
from collections.abc import Callable
from uuid import uuid4

import pytest
from sqlalchemy import func, select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.models.content import Clue, Location
from app.models.enums import Difficulty, Language, VerificationType

# locations


def test_raw_sql_location_inserts_get_the_server_defaults(db: Session) -> None:
    location = db.execute(
        text(
            "INSERT INTO locations (name, building_name, room_number, accepted_answers,"
            " gps_points, gps_radius_meters)"
            " VALUES ('Science 333', 'Science', 'SCI 333', '[\"SCI333\"]',"
            ' \'[{"lat": 49.9, "lng": -119.4}]\', 30)'
            " RETURNING id, verification_type, is_active, floor_number, created_at, updated_at"
        )
    ).one()
    assert location.id is not None
    assert location.verification_type == VerificationType.ROOM_NUMBER
    assert location.is_active is True
    assert location.floor_number is None
    assert location.created_at is not None and location.updated_at is not None


def test_each_test_location_has_its_own_room_and_answer(
    make_location: Callable[..., Location],
) -> None:
    first, second = make_location(), make_location()
    assert first.room_number != second.room_number
    assert first.accepted_answers == [str(first.room_number).replace(" ", "")]


def test_a_room_number_belongs_to_one_location(make_location: Callable[..., Location]) -> None:
    make_location(room_number="SCI 333")
    with pytest.raises(IntegrityError, match="uq_locations_room_number"):
        make_location(room_number="SCI 333")


@pytest.mark.parametrize("room_number", ["SCI 333", "ART 1234"])
def test_a_room_number_has_three_letters_and_three_or_four_digits(
    db: Session, make_location: Callable[..., Location], room_number: str
) -> None:
    location = make_location(room_number=room_number)
    db.refresh(location)
    assert location.room_number == room_number


@pytest.mark.parametrize(
    "room_number", ["SCI333", "sci 333", "SCI-333", "SC 333", "SCIE 333", "SCI 33", "SCI 33333"]
)
def test_a_room_number_not_in_the_sign_format_is_rejected(
    make_location: Callable[..., Location], room_number: str
) -> None:
    with pytest.raises(IntegrityError, match='"ck_locations_room_number_format"'):
        make_location(room_number=room_number)


def test_a_room_number_location_needs_a_room_number(
    make_location: Callable[..., Location],
) -> None:
    with pytest.raises(IntegrityError, match='"ck_locations_room_number_required"'):
        make_location(room_number=None)


@pytest.mark.parametrize("value", [[], {}, "SCI333", None])
@pytest.mark.parametrize("field", ["accepted_answers", "gps_points"])
def test_answers_and_entrances_are_non_empty_arrays(
    db: Session, make_location: Callable[..., Location], field: str, value: object
) -> None:
    # raw SQL, because the ORM's MutableList already refuses a value that is not a list
    location = make_location()
    with pytest.raises(IntegrityError, match=f'"ck_locations_{field}"'):
        db.execute(
            text(f"UPDATE locations SET {field} = CAST(:value AS jsonb) WHERE id = :id"),
            {"value": json.dumps(value), "id": location.id},
        )


def test_editing_answers_or_entrances_in_place_is_saved(
    db: Session, make_location: Callable[..., Location]
) -> None:
    location = make_location()
    location.accepted_answers.append("TST9999")
    location.gps_points.append({"lat": 49.94, "lng": -119.39})
    db.flush()
    db.expire(location)
    assert location.accepted_answers[-1] == "TST9999"
    assert len(location.gps_points) == 2


@pytest.mark.parametrize(
    ("field", "value", "constraint"),
    [
        ("name", "", "ck_locations_name_not_blank"),
        ("name", "   ", "ck_locations_name_not_blank"),
        ("building_name", " ", "ck_locations_building_name_not_blank"),
        ("gps_radius_meters", 0, "ck_locations_gps_radius_meters"),
        ("verification_type", "qr_code", "ck_locations_verification_type"),
    ],
)
def test_location_values_stay_in_range(
    make_location: Callable[..., Location], field: str, value: object, constraint: str
) -> None:
    with pytest.raises(IntegrityError, match=f'"{constraint}"'):
        make_location(**{field: value})


# clues


def test_a_test_clue_is_basic_japanese_without_a_transcript(
    db: Session, make_clue: Callable[..., Clue]
) -> None:
    clue = make_clue()
    db.refresh(clue)
    assert (clue.difficulty, clue.language, clue.text) == (Difficulty.BASIC, Language.JA, None)
    assert db.get(Location, clue.location_id) is not None


def test_a_clue_for_a_given_location_id_adds_no_other_location(
    db: Session, make_location: Callable[..., Location], make_clue: Callable[..., Clue]
) -> None:
    location = make_location()
    before = db.scalar(select(func.count()).select_from(Location))
    make_clue(location_id=location.id)
    assert db.scalar(select(func.count()).select_from(Location)) == before


def test_a_location_has_one_clue_per_difficulty_and_language(
    make_location: Callable[..., Location], make_clue: Callable[..., Clue]
) -> None:
    location = make_location()
    make_clue(location)
    make_clue(location, difficulty=Difficulty.ADVANCING)
    make_clue(location, language=Language.FR)
    with pytest.raises(IntegrityError, match="uq_clues_location_id_difficulty_language"):
        make_clue(location)


@pytest.mark.parametrize(
    ("field", "value", "constraint"),
    [
        ("difficulty", 0, "ck_clues_difficulty"),
        ("difficulty", 4, "ck_clues_difficulty"),
        ("language", "en", "ck_clues_language"),
        ("location_id", uuid4(), "fk_clues_location_id_locations"),
    ],
)
def test_clue_values_stay_in_range(
    make_clue: Callable[..., Clue], field: str, value: object, constraint: str
) -> None:
    with pytest.raises(IntegrityError, match=f'"{constraint}"'):
        make_clue(**{field: value})
