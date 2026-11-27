"""Public interface of app.content: the only module other components may import.

Content eligibility and snapshot (Interface card #12, agreed with game): which locations can be
drawn for a language and difficulty, whether a drawn location is still active, and the content
snapshot that game copies into `session_locations` when it draws a location. A location is
eligible when it is active, has a room number and has a primary clue recording in that language
and difficulty, and every feedback phrase exists in that language (otherwise a feedback URL could
fail during the game). A location's building is the code of its room number ("COM" for
"COM 316"); room numbers are stored in that form, so a building has only one spelling.
Content only decides which locations qualify; game does the picking (carry-over, preferring
uncompleted locations, shuffle exclusions, NOT_ENOUGH_CONTENT).

Eligibility is `DRAWABLE` from app.content.coverage, so the coverage report and the draw cannot
disagree. Each function reads only the columns it returns, so no `Location` is loaded into the
caller's session. Every function here only reads: it never commits, rolls back or explicitly
flushes the caller's session (the automatic flush before a query is fine).

No row is locked. A content manager may deactivate a location or move a clue's primary recording
while a game is drawing: `location_snapshot` checks eligibility again, so a location that stopped
qualifying after `eligible_location_ids` read it is refused, and game draws another. A snapshot
already taken keeps its recording, because recordings are never deleted while a game uses them.

A snapshot holds the answers. It is for the server only and must never appear in a
student-facing response, error message or log.

The clue recording in a snapshot is chosen by AudioService (app.services.audio), which also
provides the signed URLs for clue and feedback audio (Interface card #13). Game asks AudioService
for those URLs directly; they are not part of this interface. If AudioService finds no recording,
`location_snapshot` raises `LocationNotEligibleError`, the same as for any ineligible location.

`Language` and `Difficulty` are defined in app.models.enums and re-exported here.
"""

from dataclasses import dataclass, field
from typing import Any
from uuid import UUID

from sqlalchemy import Select, and_, func, select
from sqlalchemy.orm import Session

from app.content.coverage import DRAWABLE, PRIMARY_RECORDING
from app.models.content import AudioAsset, Clue, Location
from app.models.enums import Difficulty, Language
from app.services.audio import MediaNotFoundError, get_audio_service

__all__ = [
    "Difficulty",
    "GpsPoint",
    "Language",
    "LocationNotEligibleError",
    "LocationSnapshot",
    "eligible_location_buildings",
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


def _drawable(language: Language, difficulty: Difficulty | None, *columns: Any) -> Select[Any]:
    """Select drawable rows, with one row per location and matching difficulty."""
    clue = and_(Clue.location_id == Location.id, Clue.language == language)
    if difficulty is not None:
        clue = and_(clue, Clue.difficulty == difficulty)
    return (
        select(*columns)
        .select_from(Location)
        .join(Clue, clue)
        .outerjoin(AudioAsset, PRIMARY_RECORDING)
        .where(DRAWABLE)
    )


def eligible_location_ids(
    language: Language, difficulty: Difficulty | None, db: Session
) -> frozenset[UUID]:
    """IDs of every location that can be drawn for `language` and `difficulty`.

    Pass None to include locations drawable at any difficulty. Empty while the
    language is missing a feedback phrase.
    """
    return frozenset(eligible_location_buildings(language, difficulty, db))


def eligible_location_buildings(
    language: Language, difficulty: Difficulty | None, db: Session
) -> dict[UUID, str]:
    """The building of each location eligible_location_ids returns, by location ID."""
    if not get_audio_service().has_all_feedback_phrases(language, db):
        return {}
    building = func.split_part(Location.room_number, " ", 1)
    rows = db.execute(_drawable(language, difficulty, Location.id, building))
    return {location_id: code for location_id, code in rows}


def is_location_active(location_id: UUID, db: Session) -> bool:
    """False once a content manager has deactivated the location, and for an unknown ID."""
    return bool(db.scalar(select(Location.is_active).where(Location.id == location_id)))


def location_snapshot(
    location_id: UUID, language: Language, difficulty: Difficulty, db: Session
) -> LocationSnapshot:
    """The content snapshot of an eligible location; raises `LocationNotEligibleError` if not.

    The feedback phrases are not checked here: game draws only from `eligible_location_ids`,
    which checks them once per draw instead of once per location.
    """
    query = _drawable(
        language,
        difficulty,
        Clue.id,
        Location.accepted_answers,
        Location.gps_points,
        Location.gps_radius_meters,
    ).where(Location.id == location_id)
    row = db.execute(query).one_or_none()
    if row is None:
        raise LocationNotEligibleError(location_id)
    clue_id, answers, entrances, radius_meters = row
    try:
        audio_asset_id = get_audio_service().clue_audio_asset_id(clue_id, db)
    except MediaNotFoundError as error:
        # e.g. the recording was deleted after game fetched the eligible locations
        raise LocationNotEligibleError(location_id) from error
    return LocationSnapshot(
        location_id=location_id,
        clue_id=clue_id,
        audio_asset_id=audio_asset_id,
        accepted_answers=tuple(answers),
        gps_points=tuple(GpsPoint(lat=point["lat"], lng=point["lng"]) for point in entrances),
        radius_meters=radius_meters,
    )
