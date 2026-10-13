"""Tables owned by 5.0 Content (Eddy): locations and clues (audio tables follow).

Everything here is for staff and the server: location names, room numbers, accepted answers and
entrance points are the answers to the game and never reach a student.
"""

from datetime import datetime
from typing import Any
from uuid import UUID

from sqlalchemy import CheckConstraint, ForeignKey, UniqueConstraint, true
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.mutable import MutableList
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base
from app.models.columns import (
    created_at,
    difficulty_check,
    in_check,
    language_check,
    non_empty_array,
    updated_at,
    uuid_pk,
)
from app.models.enums import VerificationType

# A room number as printed on the sign: building code, a space, then the number, e.g. "SCI 333".
# The client confirmed 3 letters + 3 digits everywhere except one building, which uses 4 digits.
ROOM_NUMBER_PATTERN = "^[A-Z]{3} [0-9]{3,4}$"


class Location(Base):
    """A place students can be sent to. Never hard-deleted: deactivating it is the soft delete."""

    __tablename__ = "locations"
    __table_args__ = (
        UniqueConstraint("room_number"),  # bulk audio files name their location by room number
        # staff lists and the coverage report show these labels, so they cannot be blank
        CheckConstraint("btrim(name) <> ''", name="name_not_blank"),
        CheckConstraint("btrim(building_name) <> ''", name="building_name_not_blank"),
        in_check("verification_type", VerificationType, name="verification_type"),
        CheckConstraint(f"room_number ~ '{ROOM_NUMBER_PATTERN}'", name="room_number_format"),
        # Stage 2 reads the room-number sign, so such a location needs one
        CheckConstraint(
            f"verification_type <> '{VerificationType.ROOM_NUMBER}' OR room_number IS NOT NULL",
            name="room_number_required",
        ),
        # a location with no answers or no entrance points could never be completed
        non_empty_array("accepted_answers"),
        non_empty_array("gps_points"),
        CheckConstraint("gps_radius_meters > 0", name="gps_radius_meters"),
    )

    id: Mapped[UUID] = uuid_pk()
    name: Mapped[str]  # internal label for staff
    building_name: Mapped[str]
    floor_number: Mapped[int | None]
    verification_type: Mapped[str] = mapped_column(
        default=VerificationType.ROOM_NUMBER, server_default=VerificationType.ROOM_NUMBER.value
    )
    room_number: Mapped[str | None]  # canonical form, e.g. "SCI 333"
    # MutableList so that changing a list in place (e.g. append) is saved, as with
    # SessionLocation.flags
    # normalised as OCR text is (no space), e.g. ["SCI333"]
    accepted_answers: Mapped[list[str]] = mapped_column(MutableList.as_mutable(JSONB))
    # one {"lat": ..., "lng": ...} per building entrance
    gps_points: Mapped[list[dict[str, Any]]] = mapped_column(MutableList.as_mutable(JSONB))
    # no default here: the service fills in the gps_radius_meters setting
    gps_radius_meters: Mapped[int]
    is_active: Mapped[bool] = mapped_column(default=True, server_default=true())
    created_at: Mapped[datetime] = created_at()
    updated_at: Mapped[datetime] = updated_at()


class Clue(Base):
    """The clue for one location, difficulty and language; its recordings are audio assets."""

    __tablename__ = "clues"
    __table_args__ = (
        UniqueConstraint("location_id", "difficulty", "language"),
        difficulty_check(),
        language_check(),
    )

    id: Mapped[UUID] = uuid_pk()
    # the unique key above starts with location_id, so it also serves lookups by location
    location_id: Mapped[UUID] = mapped_column(ForeignKey("locations.id"))
    difficulty: Mapped[int]
    language: Mapped[str]
    # transcript for teachers (and future subtitles); null until entered, e.g. when a bulk audio
    # import creates the clue
    text: Mapped[str | None]
    created_at: Mapped[datetime] = created_at()
    updated_at: Mapped[datetime] = updated_at()
