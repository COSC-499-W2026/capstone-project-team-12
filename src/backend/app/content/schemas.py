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
    StringConstraints,
    model_validator,
)

from app.config import get_settings
from app.models.enums import Difficulty, FeedbackCode, Language

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


# PostgreSQL text cannot hold a NUL character, and its integer columns are 32-bit: a value
# outside either would pass here and fail on save with a 500
NO_NUL = r"^[^\x00]*$"
INT32_MAX = 2**31 - 1

NonBlank = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, pattern=NO_NUL)]
RoomNumber = Annotated[str, AfterValidator(room_number_sign)]
Answer = Annotated[str, AfterValidator(room_answer)]
Floor = Annotated[int | None, Field(ge=-INT32_MAX, le=INT32_MAX)]
Radius = Annotated[int, Field(gt=0, le=INT32_MAX)]


class EntrancePoint(BaseModel):
    """A building entrance; arriving within the radius of any entrance counts."""

    lat: float = Field(ge=-90, le=90)
    lng: float = Field(ge=-180, le=180)


class LocationFields(BaseModel):
    """Every field a content manager sets on a location, with defaults for a new one (a replace
    uses LocationReplacement).

    `accepted_answers` always starts with the room number's answer. Reactivating a deactivated
    location is a replace with `is_active` true: room numbers stay unique across inactive ones.
    """

    name: NonBlank  # internal label, never sent to students
    building_name: NonBlank
    floor_number: Floor = None
    room_number: RoomNumber
    # extra answers, e.g. for a sign that reads differently; the room number's own answer is
    # always accepted, so a typo here cannot make the location impossible to complete
    accepted_answers: list[Answer] = Field(default_factory=list)
    gps_points: list[EntrancePoint] = Field(min_length=1)
    gps_radius_meters: Radius = Field(default_factory=lambda: get_settings().gps_radius_meters)
    is_active: bool = True

    @model_validator(mode="after")
    def _answers(self) -> Self:
        answers = [room_answer(self.room_number), *self.accepted_answers]
        self.accepted_answers = list(dict.fromkeys(answers))  # drop repeats, keep the order
        return self


class LocationReplacement(LocationFields):
    """A replace (PUT) sends every field, so one left out is a 422 rather than reset to its
    default: a deactivated location made active again, extra answers dropped, the radius
    changed."""

    floor_number: Floor
    accepted_answers: list[Answer]
    gps_radius_meters: Radius
    is_active: bool


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


class ClueText(BaseModel):
    """A clue's transcript. Required, as a PUT replaces it: send null or blank to clear it."""

    text: str | None


class ClueOut(BaseModel):
    """A clue as content managers see it. Students only ever hear it."""

    model_config = ConfigDict(from_attributes=True)

    id: UUID
    location_id: UUID
    difficulty: Difficulty
    language: Language
    text: str | None


class ClueRecordingOut(BaseModel):
    """A stored clue recording. Its storage key is left out: it is random, and files are only
    served through signed media links."""

    model_config = ConfigDict(from_attributes=True)

    id: UUID
    clue_id: UUID
    is_primary: bool  # the recording new games draw
    duration_seconds: float  # of the re-encoded file
    file_size_kb: int


class CombinationCoverage(BaseModel):
    """One language and difficulty of a location in the coverage report."""

    language: Language
    difficulty: Difficulty
    has_clue_text: bool  # a transcript, for teachers; games do not need one
    has_primary_audio: bool  # the recording new games draw
    playable: bool  # new games can draw this location in this language and difficulty


class LocationCoverage(BaseModel):
    """A location, inactive ones too, with every language and difficulty."""

    id: UUID
    name: str
    room_number: str | None
    is_active: bool
    combinations: list[CombinationCoverage]  # by language, then difficulty


class DifficultyCoverage(BaseModel):
    """How many locations new games can draw from in one language and difficulty."""

    difficulty: Difficulty
    playable_locations: int


class LanguageCoverage(BaseModel):
    """What a language still lacks, and how many locations each difficulty can draw from."""

    language: Language
    # nothing in the language is playable until this is empty
    missing_feedback_codes: list[FeedbackCode]
    difficulties: list[DifficultyCoverage]  # 1 to 3


class CoverageReport(BaseModel):
    """Which location, language and difficulty combinations are playable (staff only)."""

    languages: list[LanguageCoverage]
    locations: list[LocationCoverage]
