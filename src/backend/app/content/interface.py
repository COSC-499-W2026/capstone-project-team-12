"""Public interface of app.content: the only module other components may import.

Content eligibility and snapshot (Interface card #12, agreed with game): which locations can be
drawn for a language and difficulty, whether a drawn location is still active, and the content
snapshot that game copies into `session_locations` when it draws a location. A location is
eligible when it is active, has a room number and has a primary clue recording in that language
and difficulty. Content only decides which locations qualify; game does the picking (carry-over,
preferring uncompleted locations, shuffle exclusions, NOT_ENOUGH_CONTENT).

These are stubs: the content tables do not exist yet, so they serve 30 fake locations with fixed
IDs and ignore `db`. The real versions will query `locations`, `clues` and `audio_assets`
through `db` and read each location's own `gps_radius_meters`; the signatures will not change.
Every function here only reads: it never commits, flushes or rolls back the caller's session.

A snapshot holds the answers. It is for the server only and must never appear in a
student-facing response, error message or log.

Media URLs (Interface card #13, agreed with game and accounts): signed, short-lived links to
clue and feedback audio for the game responses (`current-location`, `navigation/ping`,
`verification/photo`). The stubs sign a placeholder key: every feedback phrase exists, and a
clue recording exists for every `audio_asset_id` a stub snapshot returns. The real versions will
read `feedback_audio.file_path` and `audio_assets.file_path` through `db` and raise
`MediaNotFoundError` when no clip exists; the signatures will not change. Links are signed by
StorageService (app.services.storage), which accounts also uses to delete files; if the team
later adds the spec's AudioService (chosen by AUDIO_SERVICE_TYPE), these functions delegate to it.

`Language`, `Difficulty` and `FeedbackCode` are defined in app.models.enums and re-exported here.
"""

from dataclasses import dataclass
from uuid import NAMESPACE_URL, UUID, uuid5

from sqlalchemy.orm import Session

from app.config import get_settings
from app.models.enums import Difficulty, FeedbackCode, Language
from app.services.storage import get_storage_service

__all__ = [
    "Difficulty",
    "FeedbackCode",
    "GpsPoint",
    "Language",
    "LocationNotEligibleError",
    "LocationSnapshot",
    "MediaNotFoundError",
    "clue_audio_url",
    "eligible_location_ids",
    "feedback_audio_url",
    "is_location_active",
    "location_snapshot",
]


@dataclass(frozen=True)
class GpsPoint:
    lat: float
    lng: float


@dataclass(frozen=True)
class LocationSnapshot:
    """Content copied into `session_locations` when a location is drawn (server only)."""

    location_id: UUID
    clue_id: UUID
    audio_asset_id: UUID
    accepted_answers: tuple[str, ...]  # normalised, e.g. ("SCI333",)
    gps_points: tuple[GpsPoint, ...]  # one per building entrance, at least one
    radius_meters: int


class LocationNotEligibleError(Exception):
    """The location is not (or no longer) eligible for the requested language and difficulty."""


class MediaNotFoundError(Exception):
    """No clip exists for the requested feedback code and language, or clue audio asset."""


_STUB_LOCATION_COUNT = 30
_STUB_BUILDING_CODE = "TST"
_STUB_FIRST_ROOM = 101
_STUB_ORIGIN = GpsPoint(lat=49.9394, lng=-119.3948)  # UBCO campus
_STUB_SPACING_DEGREES = 0.0005  # about 55 m of latitude between neighbouring fake locations


def _stub_id(*parts: object) -> UUID:
    return uuid5(NAMESPACE_URL, "stub/" + "/".join(str(part) for part in parts))


_STUB_LOCATION_NUMBERS = {
    _stub_id("location", number): number for number in range(_STUB_LOCATION_COUNT)
}


def _stub_audio_asset_id(location_id: UUID, language: Language, difficulty: Difficulty) -> UUID:
    return _stub_id("audio", location_id, difficulty.value, language.value)


_STUB_AUDIO_ASSET_IDS = frozenset(
    _stub_audio_asset_id(location_id, language, difficulty)
    for location_id in _STUB_LOCATION_NUMBERS
    for language in Language
    for difficulty in Difficulty
)


def eligible_location_ids(
    db: Session, language: Language, difficulty: Difficulty
) -> frozenset[UUID]:
    """IDs of every location that can be drawn for `language` and `difficulty`."""
    return frozenset(_STUB_LOCATION_NUMBERS)


def is_location_active(db: Session, location_id: UUID) -> bool:
    """False once a content manager has deactivated the location (the stub: any unknown ID)."""
    return location_id in _STUB_LOCATION_NUMBERS


def location_snapshot(
    db: Session, location_id: UUID, language: Language, difficulty: Difficulty
) -> LocationSnapshot:
    """The content snapshot of an eligible location; raises `LocationNotEligibleError` if not."""
    number = _STUB_LOCATION_NUMBERS.get(location_id)
    if number is None:
        raise LocationNotEligibleError(location_id)
    entrance = GpsPoint(
        lat=_STUB_ORIGIN.lat + number * _STUB_SPACING_DEGREES,
        lng=_STUB_ORIGIN.lng,
    )
    return LocationSnapshot(
        location_id=location_id,
        clue_id=_stub_id("clue", location_id, difficulty.value, language.value),
        audio_asset_id=_stub_audio_asset_id(location_id, language, difficulty),
        accepted_answers=(f"{_STUB_BUILDING_CODE}{_STUB_FIRST_ROOM + number}",),
        gps_points=(entrance,),
        radius_meters=get_settings().gps_radius_meters,
    )


def feedback_audio_url(db: Session, code: FeedbackCode, language: Language) -> str:
    """Signed, short-lived URL of the spoken phrase for `code` in `language`."""
    key = _stub_id("feedback", code.value, language.value)
    return get_storage_service().signed_url(str(key))


def clue_audio_url(db: Session, audio_asset_id: UUID) -> str:
    """Signed, short-lived URL of a snapshot's clue recording; `MediaNotFoundError` if none."""
    if audio_asset_id not in _STUB_AUDIO_ASSET_IDS:
        raise MediaNotFoundError(audio_asset_id)
    return get_storage_service().signed_url(str(audio_asset_id))
