"""UBC semester validation and expiration boundaries."""

import re
from datetime import UTC, datetime
from zoneinfo import ZoneInfo

from app.errors import AppError, ErrorCode

VANCOUVER_TZ = ZoneInfo("America/Vancouver")
SEMESTER_PATTERN = re.compile(r"^([0-9]{4})(W1|W2|S1|S2)$")


def semester_expiration(semester: str) -> datetime:
    """Return the exclusive UTC expiration boundary for a UBC semester."""
    match = SEMESTER_PATTERN.fullmatch(semester)

    if match is None:
        raise AppError(
            ErrorCode.VALIDATION_ERROR,
            "Invalid semester format.",
        )

    year = int(match.group(1))
    term = match.group(2)

    if year < 1 or year > 9998:
        raise AppError(
            ErrorCode.VALIDATION_ERROR,
            "Invalid semester year.",
        )

    boundaries = {
        "W1": (year + 1, 1, 1),
        "W2": (year + 1, 5, 1),
        "S1": (year, 7, 1),
        "S2": (year, 9, 1),
    }

    expiration_local = datetime(
        *boundaries[term],
        tzinfo=VANCOUVER_TZ,
    )

    return expiration_local.astimezone(UTC)
