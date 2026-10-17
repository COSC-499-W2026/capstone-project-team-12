"""Tests for the Issue #15 engagement interface contract."""

from dataclasses import FrozenInstanceError
from uuid import uuid4

import pytest

from app.engagement.interface import EngagementResult, apply_location_result


def test_engagement_result_fields() -> None:
    """The response exposes the fields required by the specification."""
    result = EngagementResult(
        total_points=100,
        level=2,
        level_up=True,
        streak_days=3,
        challenges_completed=("complete_first_location",),
        achievements_unlocked=("first_success",),
    )

    assert result.total_points == 100
    assert result.level == 2
    assert result.level_up is True
    assert result.streak_days == 3
    assert result.challenges_completed == ("complete_first_location",)
    assert result.achievements_unlocked == ("first_success",)


def test_engagement_result_is_frozen() -> None:
    """Response fields cannot be reassigned."""
    result = EngagementResult(
        total_points=0,
        level=1,
        level_up=False,
        streak_days=0,
        challenges_completed=(),
        achievements_unlocked=(),
    )

    with pytest.raises(FrozenInstanceError):
        result.total_points = 100  # type: ignore[misc]


def test_engagement_result_collections_are_immutable() -> None:
    """Challenge and achievement collections use immutable tuples."""
    result = EngagementResult(
        total_points=100,
        level=2,
        level_up=False,
        streak_days=1,
        challenges_completed=("daily_challenge",),
        achievements_unlocked=("first_success",),
    )

    assert isinstance(result.challenges_completed, tuple)
    assert isinstance(result.achievements_unlocked, tuple)


def test_apply_location_result_returns_placeholder() -> None:
    """The stub returns a placeholder without requiring a database."""
    result = apply_location_result(
        uuid4(),
        None,  # type: ignore[arg-type]
    )

    assert result == EngagementResult(
        total_points=0,
        level=1,
        level_up=False,
        streak_days=0,
        challenges_completed=(),
        achievements_unlocked=(),
    )


def test_apply_location_result_placeholder_is_consistent() -> None:
    """Repeated stub calls return the same placeholder values."""
    session_location_id = uuid4()

    first = apply_location_result(
        session_location_id,
        None,  # type: ignore[arg-type]
    )
    second = apply_location_result(
        session_location_id,
        None,  # type: ignore[arg-type]
    )

    assert first == second
