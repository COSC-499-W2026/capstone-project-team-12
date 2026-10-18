"""Public interface of app.content: the only module other components may import.

Content eligibility and snapshot (Interface card #12, agreed with game): which locations can be
drawn for a language and difficulty, whether a drawn location is still active, and the content
snapshot that game copies into `session_locations` when it draws a location. A location is
eligible when it is active, has a room number and has a primary clue recording in that language
and difficulty. Content only decides which locations qualify; game does the picking (carry-over,
preferring uncompleted locations, shuffle exclusions, NOT_ENOUGH_CONTENT).

These are stubs: the content tables do not exist yet, so they serve 30 fake locations with fixed
IDs and ignore `db`. The real versions will query `locations` and `clues` through `db` and read
each location's own `gps_radius_meters`; the signatures will not change.
Every function here only reads: it never commits, rolls back or explicitly flushes the caller's
session (the automatic flush before a query is fine).

A snapshot holds the answers. It is for the server only and must never appear in a
student-facing response, error message or log.

The clue recording in a snapshot is chosen by AudioService (app.services.audio), which also
provides the signed URLs for clue and feedback audio (Interface card #13). Game asks AudioService
for those URLs directly; they are not part of this interface. If AudioService finds no recording,
`location_snapshot` raises `LocationNotEligibleError`, the same as for any ineligible location.

`Language` and `Difficulty` are defined in app.models.enums and re-exported here.
"""

from dataclasses import dataclass, field
from uuid import NAMESPACE_URL, UUID, uuid5

from sqlalchemy.orm import Session

from app.config import get_settings
from app.models.enums import Difficulty, Language
from app.services.audio import MediaNotFoundError, clue_audio_asset_id

__all__ = [
    "Difficulty",
    "GpsPoint",
    "Language",
    "LocationNotEligibleError",
    "LocationSnapshot",
    "eligible_location_ids",
    "is_location_active",
    "location_snapshot",
]


@dataclass(frozen=True)
class GpsPoint:
    lat: float
    lng: float


@dataclass(frozen=True)
class LocationSnapshot:
    """Content copied into `session_locations` when a location is drawn (server only).

    The answers and entrance points are left out of `repr`, so logs and tracebacks never show
    them.
    """

    location_id: UUID
    clue_id: UUID
    audio_asset_id: UUID
    accepted_answers: tuple[str, ...] = field(repr=False)  # normalised, e.g. ("SCI333",)
    gps_points: tuple[GpsPoint, ...] = field(repr=False)  # one per entrance, at least one
    radius_meters: int


class LocationNotEligibleError(Exception):
    """The location is not (or no longer) eligible for the requested language and difficulty."""


_STUB_LOCATION_COUNT = 30
_STUB_BUILDING_CODE = "TST"
_STUB_FIRST_ROOM = 101
_STUB_ORIGIN = GpsPoint(lat=49.9394, lng=-119.3948)  # UBCO campus
# About 67 m of latitude between neighbouring fake locations: more than twice the default 30 m
# radius, so no point is inside the arrival circles of two locations.
_STUB_SPACING_DEGREES = 0.0006


def _stub_id(*parts: object) -> UUID:
    return uuid5(NAMESPACE_URL, "stub/" + "/".join(str(part) for part in parts))


_STUB_LOCATION_NUMBERS = {
    _stub_id("location", number): number for number in range(_STUB_LOCATION_COUNT)
}


def eligible_location_ids(
    language: Language, difficulty: Difficulty, db: Session
) -> frozenset[UUID]:
    """IDs of every location that can be drawn for `language` and `difficulty`."""
    return frozenset(_STUB_LOCATION_NUMBERS)


def is_location_active(location_id: UUID, db: Session) -> bool:
    """False once a content manager has deactivated the location (the stub: any unknown ID)."""
    return location_id in _STUB_LOCATION_NUMBERS


def location_snapshot(
    location_id: UUID, language: Language, difficulty: Difficulty, db: Session
) -> LocationSnapshot:
    """The content snapshot of an eligible location; raises `LocationNotEligibleError` if not."""
    number = _STUB_LOCATION_NUMBERS.get(location_id)
    if number is None:
        raise LocationNotEligibleError(location_id)
    entrance = GpsPoint(
        lat=_STUB_ORIGIN.lat + number * _STUB_SPACING_DEGREES,
        lng=_STUB_ORIGIN.lng,
    )
    clue_id = _stub_id("clue", location_id, difficulty.value, language.value)
    try:
        audio_asset_id = clue_audio_asset_id(clue_id, db)
    except MediaNotFoundError as error:
        # e.g. the recording was deleted after game fetched the eligible locations
        raise LocationNotEligibleError(location_id) from error
    return LocationSnapshot(
        location_id=location_id,
        clue_id=clue_id,
        audio_asset_id=audio_asset_id,
        accepted_answers=(f"{_STUB_BUILDING_CODE}{_STUB_FIRST_ROOM + number}",),
        gps_points=(entrance,),
        radius_meters=get_settings().gps_radius_meters,
    )
