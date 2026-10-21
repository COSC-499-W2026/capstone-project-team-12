"""Tests for UBC semester expiration boundaries."""

from datetime import UTC, datetime
from zoneinfo import ZoneInfo

import pytest

from app.engagement.semester import semester_expiration
from app.errors import AppError


@pytest.mark.parametrize(
    ("semester", "year", "month", "day"),
    [
        ("2026W1", 2027, 1, 1),
        ("2026W2", 2027, 5, 1),
        ("2026S1", 2026, 7, 1),
        ("2026S2", 2026, 9, 1),
    ],
)
def test_semester_expiration_boundaries(
    semester: str,
    year: int,
    month: int,
    day: int,
) -> None:
    """Require midnight in America/Vancouver."""
    expected_local = datetime(year, month, day, tzinfo=ZoneInfo("America/Vancouver"))
    result = semester_expiration(semester)

    assert result.tzinfo == UTC
    assert result.astimezone(ZoneInfo("America/Vancouver")) == expected_local


@pytest.mark.parametrize(
    "semester",
    ["", "2026", "2026W3", "2026S3", "abcdW1", "0000S1", "9999W1"],
)
def test_invalid_semesters_raise_error(semester: str) -> None:
    """Reject unsupported formats and invalid years."""
    with pytest.raises(AppError):
        semester_expiration(semester)
