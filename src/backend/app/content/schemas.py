"""Request and response models of content management (staff only: they hold the answers).

Validation matches the CHECKs on `locations` (app.models.content), so a location that passes here
is never rejected by the database. Room numbers and answers are accepted as people type them
("sci 333", "SCI-333") and stored in canonical form: the sign text "SCI 333" and the answer
"SCI333", normalised as OCR text is (uppercase, no space or hyphen).
"""

import re
from typing import Annotated, Self
from uuid import UUID

from pydantic import (
    AfterValidator,
    BaseModel,
    ConfigDict,
    Field,
    PositiveInt,
    StringConstraints,
    model_validator,
)

from app.config import get_settings

# building code and number with at most one space or hyphen between them; ASCII only, so a
# full-width or Arabic-Indic digit is refused rather than stored
_TYPED_ROOM_NUMBER = re.compile(r"([A-Za-z]{3})[ -]?([0-9]{3,4})")


def _room_parts(text: str) -> tuple[str, str]:
    match = _TYPED_ROOM_NUMBER.fullmatch(text.strip())
    if match is None:
        raise ValueError("expected 3 letters and 3 or 4 digits, e.g. SCI 333")
    return match[1].upper(), match[2]


def room_number_sign(text: str) -> str:
    """The canonical room number of `text` ("sci-333" gives "SCI 333"), or ValueError."""
    return " ".join(_room_parts(text))


def room_answer(text: str) -> str:
    """The normalised answer of `text` ("sci 333" gives "SCI333"), or ValueError."""
    return "".join(_room_parts(text))


NonBlank = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1)]
RoomNumber = Annotated[str, AfterValidator(room_number_sign)]
Answer = Annotated[str, AfterValidator(room_answer)]


class EntrancePoint(BaseModel):
    """A building entrance; arriving within the radius of any entrance counts."""

    lat: float = Field(ge=-90, le=90)
    lng: float = Field(ge=-180, le=180)


class LocationFields(BaseModel):
    """Every field a content manager sets on a location (create, or replace with PUT).

    `accepted_answers` defaults to the room number's answer. Reactivating a deactivated location
    is a replace with `is_active` true: room numbers stay unique across inactive locations.
    """

    name: NonBlank  # internal label, never sent to students
    building_name: NonBlank
    floor_number: int | None = None
    room_number: RoomNumber
    # None is replaced by the room number's answer once the model is validated
    accepted_answers: list[Answer] | None = Field(default=None, min_length=1)
    gps_points: list[EntrancePoint] = Field(min_length=1)
    gps_radius_meters: PositiveInt = Field(default_factory=lambda: get_settings().gps_radius_meters)
    is_active: bool = True

    @model_validator(mode="after")
    def _answers(self) -> Self:
        answers = self.accepted_answers or [room_answer(self.room_number)]
        self.accepted_answers = list(dict.fromkeys(answers))  # drop repeats, keep the order
        return self


class LocationOut(BaseModel):
    """A location as content managers and other staff see it."""

    model_config = ConfigDict(from_attributes=True)

    id: UUID
    name: str
    building_name: str
    floor_number: int | None
    room_number: str | None  # only null for verification types other than room_number
    accepted_answers: list[str]
    gps_points: list[EntrancePoint]
    gps_radius_meters: int
    is_active: bool
