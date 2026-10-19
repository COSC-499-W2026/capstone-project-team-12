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
            " RETURNING verification_type, is_active, floor_number, created_at, updated_at"
        )
    ).one()
    assert location.verification_type == VerificationType.ROOM_NUMBER
    assert location.is_active is True
    assert location.floor_number is None
    assert location.created_at is not None and location.updated_at is not None


def test_a_room_number_belongs_to_one_location(make_location: Callable[..., Location]) -> None:
    make_location(room_number="SCI 333")
    with pytest.raises(IntegrityError, match="uq_locations_room_number"):
        make_location(room_number="SCI 333")


@pytest.mark.parametrize(
    "room_number", ["SCI333", "sci 333", "SCI-333", "SC 333", "SCIE 333", "SCI 33", "SCI 33333"]
)
def test_a_room_number_not_in_the_sign_format_is_rejected(
    make_location: Callable[..., Location], room_number: str
) -> None:
    with pytest.raises(IntegrityError, match='"ck_locations_room_number_format"'):
        make_location(room_number=room_number)


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


def test_a_location_at_the_format_and_range_limits_is_kept(
    db: Session, make_location: Callable[..., Location]
) -> None:
    location = make_location(
        room_number="ART 1234",  # the 4-digit form one campus building uses
        accepted_answers=["SCI333", "ART1234"],
        gps_points=[{"lat": -90, "lng": 180}, {"lat": 90.0, "lng": -180.0, "note": "side door"}],
    )
    db.refresh(location)
    assert location.room_number == "ART 1234"
    assert location.accepted_answers == ["SCI333", "ART1234"]
    assert len(location.gps_points) == 2


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
    ("field", "value", "check"),
    [
        ("name", "   ", "name_not_blank"),
        ("building_name", " ", "building_name_not_blank"),
        ("room_number", None, "room_number_required"),
        ("verification_type", "qr_code", "verification_type"),
        ("gps_radius_meters", 0, "gps_radius_meters"),
        # OCR text is compared without spaces, so this answer could never match
        ("accepted_answers", ["SCI 333"], "accepted_answers_normalised"),
        ("accepted_answers", [""], "accepted_answers_normalised"),
        ("accepted_answers", [333], "accepted_answers_normalised"),
        ("accepted_answers", [["SCI333"]], "accepted_answers_normalised"),  # strict mode
        ("gps_points", [{}], "gps_points_lat_lng"),
        ("gps_points", [{"lat": 49.9}], "gps_points_lat_lng"),
        ("gps_points", [{"lat": [49.9], "lng": 0}], "gps_points_lat_lng"),  # not unwrapped
        ("gps_points", [[{"lat": 49.9, "lng": 0}]], "gps_points_lat_lng"),  # strict mode
        ("gps_points", [{"lat": 90.1, "lng": 0}], "gps_points_lat_lng"),
        ("gps_points", [{"lat": -90.1, "lng": 0}], "gps_points_lat_lng"),
        ("gps_points", [{"lat": 0, "lng": 180.1}], "gps_points_lat_lng"),
        ("gps_points", [{"lat": 0, "lng": -180.1}], "gps_points_lat_lng"),
    ],
)
def test_location_values_stay_in_range(
    make_location: Callable[..., Location], field: str, value: object, check: str
) -> None:
    with pytest.raises(IntegrityError, match=f'"ck_locations_{check}"'):
        make_location(**{field: value})


# clues


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
