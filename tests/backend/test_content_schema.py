"""The content tables (locations, clues, audio_assets, feedback_audio) reject bad data at the
database level."""

import json
from collections.abc import Callable
from uuid import uuid4

import pytest
from sqlalchemy import func, select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.models.accounts import User
from app.models.content import AudioAsset, Clue, FeedbackAudio, Location
from app.models.enums import (
    AudioSourceType,
    Difficulty,
    FeedbackCode,
    Language,
    UserRole,
    VerificationType,
)

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


def test_several_normalised_answers_and_entrances_at_the_range_limits_are_kept(
    db: Session, make_location: Callable[..., Location]
) -> None:
    location = make_location(
        accepted_answers=["SCI333", "ART1234"],
        gps_points=[{"lat": -90, "lng": 180}, {"lat": 90.0, "lng": -180.0, "note": "side door"}],
    )
    db.refresh(location)
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


# audio_assets


def test_raw_sql_audio_inserts_get_the_server_defaults(
    db: Session, make_clue: Callable[..., Clue]
) -> None:
    asset = db.execute(
        text(
            "INSERT INTO audio_assets (clue_id, file_path, provider, duration_seconds,"
            " file_size_kb) VALUES (:clue_id, 'a.mp3', 'teacher', 4.5, 70)"
            " RETURNING id, source_type, is_primary, uploaded_by, created_at"
        ),
        {"clue_id": make_clue().id},
    ).one()
    assert asset.id is not None
    assert asset.source_type == AudioSourceType.UPLOADED
    assert asset.is_primary is False
    assert asset.uploaded_by is None
    assert asset.created_at is not None


def test_an_upload_records_who_uploaded_it(
    db: Session, make_user: Callable[..., User], make_audio_asset: Callable[..., AudioAsset]
) -> None:
    manager = make_user(role=UserRole.STAFF, can_manage_content=True)
    asset = make_audio_asset(uploaded_by=manager.id)
    db.refresh(asset)
    assert asset.uploaded_by == manager.id


def test_a_clue_has_at_most_one_primary_recording(
    make_clue: Callable[..., Clue], make_audio_asset: Callable[..., AudioAsset]
) -> None:
    clue = make_clue()
    make_audio_asset(clue, is_primary=True)
    make_audio_asset(clue)  # older recordings stay, just not primary
    make_audio_asset(clue)
    with pytest.raises(IntegrityError, match="uq_audio_assets_one_primary_per_clue"):
        make_audio_asset(clue, is_primary=True)


def test_each_clue_can_have_its_own_primary_recording(
    make_audio_asset: Callable[..., AudioAsset],
) -> None:
    first, second = make_audio_asset(is_primary=True), make_audio_asset(is_primary=True)
    assert first.clue_id != second.clue_id


def test_the_primary_recording_can_be_replaced(
    db: Session, make_clue: Callable[..., Clue], make_audio_asset: Callable[..., AudioAsset]
) -> None:
    clue = make_clue()
    old = make_audio_asset(clue, is_primary=True)
    old.is_primary = False
    db.flush()  # the old one gives up the slot before the new one takes it
    new = make_audio_asset(clue, is_primary=True)
    assert (old.is_primary, new.is_primary) == (False, True)


def test_a_storage_key_belongs_to_one_recording(
    make_audio_asset: Callable[..., AudioAsset],
) -> None:
    first = make_audio_asset()
    with pytest.raises(IntegrityError, match="uq_audio_assets_file_path"):
        make_audio_asset(file_path=first.file_path)


@pytest.mark.parametrize(
    ("field", "value", "constraint"),
    [
        ("source_type", "recorded", "ck_audio_assets_source_type"),
        ("duration_seconds", 0, "ck_audio_assets_duration_seconds"),
        ("file_size_kb", 0, "ck_audio_assets_file_size_kb"),
        ("clue_id", uuid4(), "fk_audio_assets_clue_id_clues"),
        ("uploaded_by", uuid4(), "fk_audio_assets_uploaded_by_users"),
    ],
)
def test_audio_values_stay_in_range(
    make_audio_asset: Callable[..., AudioAsset], field: str, value: object, constraint: str
) -> None:
    with pytest.raises(IntegrityError, match=f'"{constraint}"'):
        make_audio_asset(**{field: value})


# feedback_audio


def test_a_phrase_can_be_recorded_in_every_language(
    db: Session, make_feedback_audio: Callable[..., FeedbackAudio]
) -> None:
    for language in Language:
        make_feedback_audio(FeedbackCode.VERIFY_SUCCESS, language)
    count = select(func.count()).where(FeedbackAudio.code == FeedbackCode.VERIFY_SUCCESS)
    assert db.scalar(count) == len(Language)


def test_a_phrase_has_one_recording_per_language(
    make_feedback_audio: Callable[..., FeedbackAudio],
) -> None:
    make_feedback_audio(FeedbackCode.NAV_ARRIVED, Language.FR)
    with pytest.raises(IntegrityError, match="pk_feedback_audio"):
        make_feedback_audio(FeedbackCode.NAV_ARRIVED, Language.FR)


def test_a_phrase_storage_key_belongs_to_one_phrase(
    make_feedback_audio: Callable[..., FeedbackAudio],
) -> None:
    first = make_feedback_audio(FeedbackCode.NAV_START)
    with pytest.raises(IntegrityError, match="uq_feedback_audio_file_path"):
        make_feedback_audio(FeedbackCode.NAV_ARRIVED, file_path=first.file_path)


@pytest.mark.parametrize(
    ("code", "language", "constraint"),
    [
        ("NAV_LOST", Language.JA, "ck_feedback_audio_code"),
        (FeedbackCode.NAV_START, "en", "ck_feedback_audio_language"),
    ],
)
def test_only_known_phrases_and_languages_are_stored(
    make_feedback_audio: Callable[..., FeedbackAudio], code: str, language: str, constraint: str
) -> None:
    with pytest.raises(IntegrityError, match=f'"{constraint}"'):
        make_feedback_audio(code, language)
