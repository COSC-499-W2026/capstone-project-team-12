"""5.1 Manage locations and 5.2 Edit clue text (A20).

Locations are never deleted: deactivating one is the soft delete, and a replace can reactivate
it. Clues are created on demand, the first time a location gets text or audio in a language and
difficulty. Edits only change new games: a drawn location is a snapshot (spec, Content Changes
During a Session).

These functions flush so the caller sees new IDs and constraint errors, and never commit: the
router owns the transaction. Unknown location IDs raise NOT_FOUND.
"""

from uuid import UUID

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.content.schemas import LocationFields
from app.db import violated_constraint
from app.errors import AppError, ErrorCode
from app.models.content import Clue, Location
from app.models.enums import Difficulty, Language


def list_locations(db: Session) -> list[Location]:
    """Every location, inactive ones too (so they can be reactivated), by room number.

    Not paginated: a campus has a few dozen locations.
    """
    return list(db.scalars(select(Location).order_by(Location.room_number, Location.name)))


def create_location(fields: LocationFields, db: Session) -> Location:
    """Add an active (unless `fields` says otherwise) location; VALIDATION_ERROR if its room
    number is already used, by an inactive location too."""
    location = Location(**fields.model_dump())
    db.add(location)
    _flush(fields.room_number, db)
    return location


def replace_location(location_id: UUID, fields: LocationFields, db: Session) -> Location:
    """Replace every field of the location, `is_active` included; VALIDATION_ERROR if another
    location has the room number."""
    location = _get_location(location_id, db)
    for name, value in fields.model_dump().items():
        setattr(location, name, value)
    _flush(fields.room_number, db)
    return location


def deactivate_location(location_id: UUID, db: Session) -> None:
    """Soft delete: new games stop drawing the location. Deactivating it again does nothing."""
    _get_location(location_id, db).is_active = False
    db.flush()


def set_clue_text(
    location_id: UUID, difficulty: Difficulty, language: Language, text: str | None, db: Session
) -> Clue:
    """Set the clue's transcript, creating the clue if needed. A blank transcript is stored as
    null (no transcript yet)."""
    _get_location(location_id, db)
    clue = get_or_create_clue(location_id, difficulty, language, db)
    clue.text = (text or "").strip() or None
    db.flush()
    return clue


def get_or_create_clue(
    location_id: UUID, difficulty: Difficulty, language: Language, db: Session
) -> Clue:
    """The clue of an existing location for `difficulty` and `language`, created if missing.

    The insert skips an existing clue, so two requests creating the same clue both get it.
    """
    key = {"location_id": location_id, "difficulty": difficulty, "language": language}
    db.execute(insert(Clue).values(key).on_conflict_do_nothing(index_elements=list(key)))
    return db.scalars(select(Clue).filter_by(**key)).one()


def _get_location(location_id: UUID, db: Session) -> Location:
    location = db.get(Location, location_id)
    if location is None:
        raise AppError(ErrorCode.NOT_FOUND, "Location not found")
    return location


def _flush(room_number: str, db: Session) -> None:
    """Flush the location; VALIDATION_ERROR if another location has `room_number`.

    The unique constraint is the check, so two requests saving the same room number at once
    cannot both succeed. The spec has no error code for a duplicate room number. The failed
    flush rolls the session back, so a caller saving several locations (the CSV import) must
    find duplicates before saving rather than through this error.
    """
    try:
        db.flush()
    except IntegrityError as error:
        if violated_constraint(error) != "uq_locations_room_number":
            raise
        message = f"Another location already has room number {room_number}"
        details = [{"field": "room_number", "message": message}]
        raise AppError(ErrorCode.VALIDATION_ERROR, message, details) from error
