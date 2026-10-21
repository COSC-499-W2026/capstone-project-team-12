"""Tests for UBC semester expiration boundaries."""

from datetime import UTC, datetime

import pytest

from app.engagement.semester import semester_expiration
from app.errors import AppError


@pytest.mark.parametrize(
    ("semester", "expected"),
    [
        ("2026W1", datetime(2027, 1, 1, 7, tzinfo=UTC)),
        ("2026W2", datetime(2027, 5, 1, 7, tzinfo=UTC)),
        ("2026S1", datetime(2026, 7, 1, 7, tzinfo=UTC)),
        ("2026S2", datetime(2026, 9, 1, 7, tzinfo=UTC)),
    ],
)
def test_semester_expiration_boundaries(
    semester: str,
    expected: datetime,
) -> None:
    """Use Vancouver midnight, converted to UTC."""
    assert semester_expiration(semester) == expected


@pytest.mark.parametrize(
    "semester",
    ["", "2026", "2026W3", "2026S3", "abcdW1", "0000S1", "9999W1"],
)
def test_invalid_semesters_raise_error(semester: str) -> None:
    """Reject unsupported formats and invalid years."""
    with pytest.raises(AppError):
        semester_expiration(semester)
