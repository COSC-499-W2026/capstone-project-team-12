
"""Tests for the Issue #15 engagement interface contract."""

from dataclasses import FrozenInstanceError
from uuid import uuid4

import pytest

from app.engagement.interface import EngagementResult, apply_location_result


def test_engagement_result_fields() -> None:
    """The response exposes the expected engagement fields."""
    result = EngagementResult(
        points=100,
        level=2,
        streak=3,
        challenges=["complete_first_location"],
        badges=["first_success"],
    )

    assert result.points == 100
    assert result.level == 2
    assert result.streak == 3
    assert result.challenges == ["complete_first_location"]
    assert result.badges == ["first_success"]


def test_engagement_result_is_frozen() -> None:
    """The response fields cannot be reassigned."""
    result = EngagementResult(
        points=0,
        level=1,
        streak=0,
        challenges=[],
        badges=[],
    )

    with pytest.raises(FrozenInstanceError):
        result.points = 100  # type: ignore[misc]


def test_apply_location_result_is_unimplemented() -> None:
    """The stub raises NotImplementedError without requiring a database."""
    with pytest.raises(
        NotImplementedError,
        match="Issue #15: engagement processing is not implemented",
    ):
        apply_location_result(uuid4(), None)  # type: ignore[arg-type]
