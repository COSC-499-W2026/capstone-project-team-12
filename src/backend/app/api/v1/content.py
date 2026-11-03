"""Content management (A20): locations, for staff only.

Room numbers and accepted answers are the game's answers, so only staff accounts may read them:
students, including students who are a class's TA, and admins get 403. Changes also need content
rights (`can_manage_content`).
"""

from http import HTTPStatus
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.accounts.interface import can_manage_content, require_staff
from app.content.locations import (
    create_location,
    deactivate_location,
    list_locations,
    replace_location,
)
from app.content.schemas import LocationFields, LocationOut, LocationReplacement
from app.db import get_db
from app.errors import error_responses
from app.models.content import Location

content_router = APIRouter(prefix="/content", tags=["content"])

# reads are for any staff account; content rights can only be given to staff (a CHECK on users)
STAFF = [Depends(require_staff)]
CONTENT_MANAGER = [Depends(can_manage_content)]
ACCESS_ERRORS = (HTTPStatus.UNAUTHORIZED, HTTPStatus.FORBIDDEN)


@content_router.get(
    "/locations",
    operation_id="listLocations",
    summary="List locations",
    dependencies=STAFF,
    response_model=list[LocationOut],
    responses=error_responses(*ACCESS_ERRORS),
)
def get_locations(db: Annotated[Session, Depends(get_db)]) -> list[Location]:
    """Every location, deactivated ones too (so they can be brought back), by room number.

    Not paged: a campus has a few dozen locations. Any staff account may read them; 403
    FORBIDDEN_ROLE for students (TAs included, since room numbers are the answers) and admins.
    """
    return list_locations(db)


@content_router.post(
    "/locations",
    operation_id="createLocation",
    summary="Add a location",
    status_code=HTTPStatus.CREATED,
    dependencies=CONTENT_MANAGER,
    response_model=LocationOut,
    responses=error_responses(*ACCESS_ERRORS),
)
def add_location(fields: LocationFields, db: Annotated[Session, Depends(get_db)]) -> Location:
    """Add a location, active unless `is_active` is false.

    The room number may be typed as `sci333`, `SCI-333` or `SCI 333`; it is stored as printed on
    the sign (`SCI 333`). `accepted_answers` always starts with the room number's own answer
    (`SCI333`), without duplicates; send only extra answers, or leave it out. The radius defaults
    to the `GPS_RADIUS_METERS` setting. 422 VALIDATION_ERROR (on `room_number`) if another
    location, a deactivated one included, has the room number. Needs content rights: 403
    FORBIDDEN_ROLE otherwise.
    """
    location = create_location(fields, db)
    db.commit()
    return location


@content_router.put(
    "/locations/{location_id}",
    operation_id="replaceLocation",
    summary="Replace a location",
    dependencies=CONTENT_MANAGER,
    response_model=LocationOut,
    responses=error_responses(*ACCESS_ERRORS, HTTPStatus.NOT_FOUND),
)
def put_location(
    location_id: UUID, fields: LocationReplacement, db: Annotated[Session, Depends(get_db)]
) -> Location:
    """Replace every field of a location, checked as for `createLocation`. Every field must be
    sent (`floor_number` may be null, `accepted_answers` empty), so nothing is reset by being left
    out; `is_active` true brings a deactivated location back.

    Changes only affect new games: a game in progress keeps the copy it drew. 404 NOT_FOUND for
    an unknown id; 422 VALIDATION_ERROR for a missing field or if another location has the room
    number. Needs content rights: 403 FORBIDDEN_ROLE otherwise.
    """
    location = replace_location(location_id, fields, db)
    db.commit()
    return location


@content_router.delete(
    "/locations/{location_id}",
    operation_id="deleteLocation",
    summary="Deactivate a location",
    status_code=HTTPStatus.NO_CONTENT,
    dependencies=CONTENT_MANAGER,
    responses=error_responses(*ACCESS_ERRORS, HTTPStatus.NOT_FOUND),
)
def delete_location(location_id: UUID, db: Annotated[Session, Depends(get_db)]) -> None:
    """Soft delete: the location stays, with its clues and recordings, but is marked inactive so
    new games do not draw it. `replaceLocation` with `is_active` true brings it back. Deleting it
    again changes nothing. 404 NOT_FOUND for an unknown id. Needs content rights: 403
    FORBIDDEN_ROLE otherwise.
    """
    deactivate_location(location_id, db)
    db.commit()
